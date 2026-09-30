"""Execute in a disposable process with a hard deadline and no external access."""
import hashlib
import json
import multiprocessing as mp
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from sentinel.config import QUERY_TIMEOUT_SECONDS
from sentinel.nlq.sql_guard import guard_sql


@dataclass
class QueryResult:
    status: str
    rows: list
    sql: str
    parameters: dict
    elapsed_ms: float
    explanation: str
    evidence_id: str = ""
    truncated: bool = False


def _worker(pipe, database, sql, parameters, limit):
    import duckdb
    try:
        with duckdb.connect(database, read_only=True, config={"enable_external_access": "false", "autoinstall_known_extensions": "false", "autoload_known_extensions": "false", "threads": "1", "memory_limit": "128MB"}) as con:
            cursor = con.execute(sql, parameters)
            names = [x[0] for x in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchmany(limit)]
            # JSON serialization freezes date values and rejects NaN.
            pipe.send(("ok", json.loads(json.dumps(rows, default=str, allow_nan=False))))
    except Exception as exc:
        pipe.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        pipe.close()


def execute(database, sql, parameters=None, timeout=QUERY_TIMEOUT_SECONDS):
    started = time.perf_counter()
    checked = guard_sql(sql, parameters)
    if timeout <= 0:
        return QueryResult("timeout", [], checked.sql, checked.parameters, 0.0, "Query deadline expired before execution.")
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, str(Path(database).resolve()), checked.sql, checked.parameters, checked.limit), daemon=True)
    process.start()
    child.close()
    try:
        if parent.poll(timeout):
            try:
                status, payload = parent.recv()
            except EOFError:
                status, payload = "error", "Query worker exited without a result."
        else:
            status, payload = "timeout", "Query exceeded its deadline; the worker was terminated."
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
    elapsed = (time.perf_counter()-started)*1000
    if status != "ok":
        return QueryResult(status, [], checked.sql, checked.parameters, elapsed, payload)
    digest = hashlib.sha256(json.dumps({"sql": checked.sql, "parameters": checked.parameters, "rows": payload}, sort_keys=True).encode()).hexdigest()
    return QueryResult("ok" if payload else "empty", payload, checked.sql, checked.parameters, elapsed,
                       "Verified read-only result." if payload else "No matching records; no recommendation was inferred.", digest, len(payload) == checked.limit)
