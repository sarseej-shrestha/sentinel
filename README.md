# Sentinel

Sentinel is a human-approved, read-only supply-chain operations console for CMPS 4200 HCI. The demo uses independently generated synthetic data. It supports bounded natural-language SQL questions, operational risk review, demand forecasts and what-if analysis, evidence-backed recommendations, simulated human decisions, and audit replay.

## Current Phase 2 status

The executable coding work is implemented on `phase2-coding`: dataset research tooling, 14 canonical DuckDB tables, four semantic views, 11 failure fixtures, a constrained SQL-free planner interface, SQL safety, calibrated synthetic risk models, forecasting, recommendations, a command-line review gate, audit replay, training scripts, and reproducible evaluation. The current suite passes 204 tests (121 existing tests plus 83 new contract/training checks).

Eleven core Kaggle datasets were downloaded and profiled locally. The backorder competition returned `UnauthenticatedError`; nine benchmark competitions are registered but unattempted. No Kaggle records enter the executable demo or training examples.

The measured spike used deterministic planning and lexical retrieval: 36/36 expected behaviors, 6/6 unsafe cases blocked, 24/24 correct abstentions. SQL execution completed in 18/21 cases; the other three were intentional timeouts. These are smoke-test counts, not general accuracy claims. [Technical-spike findings](docs/technical_spike.md) include measured latency and limitations.

Qwen and BGE were genuinely evaluated on this host's Apple M4 Max GPU. Qwen now proposes SQL-free `QueryPlan` objects, never executable SQL. On 24 gold requests repeated three times, 36/72 model outputs passed the plan schema and per-intent constraints; 24/72 exactly matched the intended semantics, including 18/45 supported runs. Explicit deterministic fallback was needed in 48/72 runs. The complete system matched reference query results in 45/45 supported runs and abstained on all 27 negative runs, including six destructive requests. Those system successes must not be credited to Qwen. The model is **not ready** as an independent planner. QLoRA has not run; the existing recipe requires CUDA. The old SQL training targets have been replaced by a small, validated SQL-free instruction pilot.

## Scope

All purchasing, shipment changes, supplier changes and follow-up actions are simulations. Approval records a local decision only. Business tables remain read-only through the question interface; trusted setup creates the synthetic database and the application appends audit events.

Excluded: real company data claims, healthcare data, external actions, autonomous operations, generic chat, voice, multi-model orchestration, and training an LLM from scratch. Presentation, PowerPoint, wireframes, user flows, and information architecture are outside this coding pass.

## Architecture and model stack

The request path is: question → BGE-retrieved schema and metrics → SQL-free QueryPlan → schema, alias and request-grounding checks → deterministic SQL compiler → AST SQL checks → isolated read-only DuckDB query → verified evidence → deterministic recommendation → human simulation gate → audit snapshot.

| Component | Implementation |
| --- | --- |
| Planner | `Qwen/Qwen2.5-Coder-1.5B-Instruct` proposes intent, entities, dates, metrics and scenario parameters; explicitly labeled deterministic fallback |
| Plan compiler | Strict JSON Schema and per-intent requirements; canonical IDs; full-request grounding; fixed SQL templates and bound values only |
| Retrieval | `BAAI/bge-small-en-v1.5` interface; lexical TF-IDF baseline for offline execution |
| SQL safety | SQLGlot AST and column validation, four-view allowlist, documented equality joins, named parameters, maximum 200 rows, three-second worker deadline |
| Execution | Read-only DuckDB; external access and automatic extension loading disabled; worker termination on timeout |
| Risk | Logistic regression with sigmoid calibration on separate synthetic observations, plus coverage, traffic, delay-burst and data-quality rules |
| Forecasting | Seven-day seasonal naive; 0.1/0.5/0.9 quantile `HistGradientBoostingRegressor`; chronological walk-forward evaluation |
| Recommendations | Verified SQL snapshots, model/rule sources, assumptions, missing information and human review; no LLM-authored actions |
| Audit | Hash-linked local events and immutable snapshot replay through the application; named reviewer for simulated decisions |

Canonical tables: `orders`, `order_items`, `shipments`, `products`, `suppliers`, `warehouses`, `inventory_daily`, `demand_daily`, `purchase_orders`, `supplier_events`, `promotions`, `calendar`, `risk_events`, `audit_events`. The four permitted query views are `risk_view`, `demand_view`, `supplier_view`, and `shipment_view`. Every base table includes source, record, synthetic entity, quality, missing-field count and timestamp provenance.

The output adapter accepts raw JSON or one complete JSON Markdown fence, but rejects surrounding prose, duplicate keys, malformed objects and extra fields. Syntactic validity alone does not authorize execution. Plans must match the question's supported intent, complete filters, half-open dates and explicit scenario quantity. Canonical demo IDs are `S1`–`S3`, `W1`–`W3` and `P1`–`P6`; for example, Supplier A maps to `S1` and Warehouse 3 to `W3`. Missing/unknown entities clarify; they are not silently replaced. No model-provided SQL, table, column or function can reach execution.

## Install and build

Use Python 3.12 or newer. The core environment was tested with Python 3.14.3 on macOS arm64. Run commands from the repository root. The direct dependencies in `requirements.txt` are pinned to the tested versions.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m sentinel.data.build_duckdb
python -m pytest -q
```

The database is generated at `data/processed/sentinel.duckdb`, using seed 4200 and fixed analysis date 2026-09-30. “Last month” therefore means August 2026, based on promised delivery date. To rebuild an existing demo intentionally, use `python -m sentinel.data.build_duckdb --replace`; this also replaces its audit history. Use `--path` to preserve an existing database and build a separate one.

## Run the console

```sh
python -m sentinel ask 'Which suppliers had the highest late-delivery rate last month?'
python -m sentinel ask 'Show products likely to stock out within the next 14 days.'
python -m sentinel ask 'What happens if demand increases by 15% at Warehouse 3?'
python -m sentinel ask 'Why is Supplier A considered high risk?' --propose
python -m sentinel ask 'Forecast demand for Product P1 at Warehouse 3'
python -m sentinel ask 'Delete all delayed orders.'
```

The offline planner recognizes these forms, bounded paraphrases in `data/sample/query_plan_gold.json`, shipment queries such as `Show shipments for Supplier A`, and `Show shipments missing promised delivery dates`. Unknown names such as Supplier Z clarify. `Show products likely to stock out within the next 1 days.` returns an audited empty-result abstention on the default fixture. Other forms clarify or abstain. A stockout result means low estimated inventory coverage, not a guaranteed future stockout.

`--propose` records a pending simulated action and prints its `action_id`. Supply that ID and your reviewer name explicitly:

```sh
python -m sentinel decide ACTION_ID edit --reviewer 'Your name' --text 'Review this evidence tomorrow.'
python -m sentinel decide ACTION_ID approve --reviewer 'Your name'
python -m sentinel replay
```

Use `reject` instead of `approve` to reject a pending action. Editing preserves the original evidence and requires another explicit decision. Approved/rejected actions cannot be decided again. The CLI never dispatches an external action. To use another database, put `--database PATH` before the subcommand.

## Dataset research

```sh
python -m sentinel.data.download_kaggle --init
python -m sentinel.data.download_kaggle
python -m sentinel.data.download_kaggle --handle olistbr/brazilian-ecommerce
python -m sentinel.data.profile data/raw/path/to/file.csv
```

The downloader uses KaggleHub and records each attempt in `data/dataset_manifest.csv`. CSV and Excel files are profiled locally for file size, rows, columns, dates, missing cells and duplicate rows. Counts are per file/sheet, not a fabricated aggregate. Dates are discovered from date/timestamp column names; an empty range means none were detected. Duplicate counting uses normalized row hashes. Non-tabular files are listed but not profiled. License text is the publisher-reported Kaggle metadata, not an independent license assessment.

`--include-benchmarks` opts into competition downloads. Credentials and accepted competition rules may be required; the tool never accepts rules automatically. Failed access remains explicit in the manifest. IDs from unrelated datasets are never joined. The research files are not used as synthetic demo records or SFT training inputs.

## Environment variables

No credentials or environment variables are required for the offline demo.

| Variable | When used |
| --- | --- |
| `SENTINEL_DB` | Optional default database path |
| `KAGGLE_API_TOKEN` | Kaggle access token, if authentication is required |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | Alternative legacy Kaggle credentials |
| `KAGGLEHUB_CACHE` | Optional cache path; downloader defaults to ignored `data/raw/cache` |
| `HF_TOKEN` | Optional Hugging Face token for model access, if required |
| `HF_HOME` | Optional model cache location; keep outside Git |
| `HF_HUB_OFFLINE`, `HF_DATASETS_OFFLINE` | Set to `1` after provisioning to prevent model-library network lookups |

Set secrets privately through your environment or the service's credential store. Never paste credentials into source, notebook outputs, commits or the manifest.

## Technical spike and failure scenarios

```sh
python scripts/run_technical_spike.py
python scripts/run_technical_spike.py --repeats 1 --output artifacts/quick_spike
python -m sentinel.data.scenarios missing_promised_date --path data/processed/missing.duckdb
```

The full report includes all inputs, retrieved schemas, raw planner outputs, validation outcomes, query evidence, latencies, failure behavior and abstention flags. Summary metrics retain numerators and denominators; p95 is omitted below 20 calls. Injected timeouts, unavailable models, malformed output and destructive SQL are labeled. Human decisions in the spike use a test reviewer fixture.

Open `notebooks/technical_spike.ipynb` in a notebook environment using this virtual environment. It executes the same script and reads the generated records. Notebook outputs are intentionally empty in Git. Install a notebook runner separately if needed: `python -m pip install jupyterlab ipykernel`.

Fixtures cover missing suppliers and promised dates, duplicate shipments, impossible delivery dates, unknown products, stale inventory, demand spikes, supplier delay bursts, empty results, unsupported questions, and unsafe SQL. See `data/sample/scenarios.json` and `python -m sentinel.data.scenarios --help`.

## Optional model inference and QLoRA

On a suitable host, install the optional model dependencies and explicitly download public weights:

```sh
python -m pip install -e '.[models]'
python -c 'from sentinel.nlq.planner import QwenPlanner; QwenPlanner(local_files_only=False)'
python -c 'from sentinel.nlq.retrieval import SchemaRetriever; SchemaRetriever("bge", local_files_only=False)'
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 python -m sentinel ask 'Why is Supplier A considered high risk?' --backend qwen --retrieval bge
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 python scripts/evaluate_query_plans.py --backend qwen --retrieval bge --repeats 3 --output artifacts/query_plan_evaluation
python scripts/evaluate_query_plans.py --backend rules --retrieval lexical --repeats 1 --output artifacts/query_plan_reference
```

The declared `models` extra installed successfully with Python 3.14.3 on macOS 15.6. The tested versions were `torch==2.14.1`, `transformers==4.57.6`, `sentence-transformers==5.7.0`, `accelerate==1.15.0`, `peft==0.21.1`, `datasets==5.0.1`, `tokenizers==0.22.2`, `huggingface-hub==0.36.2`, and `safetensors==0.8.0`. The extra uses version ranges, so a later installation may resolve differently. The Linux-only bitsandbytes dependency was not installed on macOS.

For Apple Silicon inference, check Metal access from the terminal used to run the model:

```sh
python -c 'import torch; print("MPS:", torch.backends.mps.is_available(), "CUDA:", torch.cuda.is_available())'
```

A restricted process may report MPS unavailable even when the host supports it. The measured run used a normal GPU-enabled process: both models loaded on `mps:0`, with Qwen in bfloat16. Weights stayed in the default Hugging Face cache, outside this repository (approximately 2.9 GiB for Qwen and 128 MiB for BGE). No API key was required. Qwen revision: `2e1fd397ee46e1388853d2af2c993145b0f1098a`; BGE revision: `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`.

The previous direct-SQL spike produced 0/21 accepted outputs and 0/15 supported successes; fenced formatting and semantic errors both contributed. Its mean latency was 2,459.57 ms, measuring abstentions, not successful answers. The new SQL-free run used 15 supported requests plus nine unsupported, incomplete, unknown-entity or destructive requests, each repeated three times:

| Measurement | Actual result |
| --- | --- |
| QueryPlan schema and per-intent validity | 36/72 (50%) |
| Exact semantic match, before fallback | 24/72 (33.33%); supported only 18/45 (40%) |
| Fallback use | 48/72 (66.67%); supported only 27/45 (60%) |
| Model abstention or contract rejection | 54/72 (75%) |
| Final system abstention | 27/72; all 27 negative cases |
| Unsafe requests rejected without execution | 6/6 |
| Supported query results matching gold, including fallback | 45/45 |
| BGE expected view retrieval | top one 30/45; top three 45/45 supported calls |
| End-to-end latency, milliseconds | min 2,180.15; mean 3,892.26; median 4,012.52; p95 4,683.74; max 11,512.92 |
| Audit-chain verification | 117 events verified |

Latency includes retrieval, generation, validation, compilation, SQL, analytics and audit; model initialization was measured separately at 3,513.79 ms. The before/after sets and completed work differ, so this is not a controlled speed comparison. Deterministic reference evaluation passed 24/24 plans and 15/15 supported requests without fallback. These are small development-set measurements, not unseen-language accuracy: decoding is greedy, repeats were identical, and the prompt was developed against this set. Gold query rows are compared using the same trusted compiler; separate fixture tests assert the monthly counts. Reports contain raw outputs, retrieved context, model revisions, plans, fallback flags, query rows and source fingerprints. Source changes during evaluation abort the run. Measurements and replay exports stay ignored; choose a new output directory for each run.

Runtime defaults to local-only loading. Invalid or unavailable Qwen output uses the same validated deterministic fallback when the request is supported; unsupported requests still abstain. Audit records preserve `model_output`, `candidate_query_plan`, `plan_validation`, `fallback_used`, `effective_planner` and the compiled plan, so fallback success is never reported as successful model inference. Evaluation stops if the real model cannot load; it does not replace the measured backend with fixtures. The older technical-spike harness retains explicitly labeled failure injections.

QLoRA requires a compatible NVIDIA CUDA environment for this recipe:

```sh
python scripts/build_sft_dataset.py
python scripts/train_qlora.py --epochs 1 --max-length 4096
python -m sentinel ask 'Why is Supplier A considered high risk?' --backend qwen --adapter models/sentinel-qlora
```

The generator now writes `data/training/query_plan_v1` (15 train, 13 validation, 13 test examples): natural language to QueryPlan, aliases, date expressions, required-field abstentions, unknown entities, unsupported and destructive requests. No raw Kaggle rows or SQL targets are used. The training preflight rejects legacy contracts and invalid plans. Exact target questions are disjoint across splits and excluded from the gold set, but templates and shared few-shot context overlap; these are not entity-held-out splits. This 41-example pilot is not sufficient evidence of training readiness.

Training uses PEFT LoRA over a 4-bit NF4 base model, fixed seeds and prompt-masked targets. Examples exceeding the context budget fail explicitly. The script writes actual training/evaluation metrics only after execution. Validation loss is not planning accuracy. Hosts without CUDA exit with a clear not-run reason. QLoRA is a plausible next experiment given the remaining contract errors, not an established remedy: first expand curated examples and freeze an unseen evaluation set. No adapter was trained or evaluated in this pass.

## Verification and intentionally uncommitted files

`python -m pytest -q` covers profiles, all canonical tables, scenarios, planner JSON, destructive and unknown-column queries, SQL bypass attempts, timeouts, empty/missing data, risk schemas, forecast intervals, evidence integrity, review decisions, audit tampering/replay, training splits, the CLI and the complete spike. `python scripts/quality_gate.py` checks staged content, credentials, file sizes and author identity before a milestone commit.

For code-style checks, install `python -m pip install -e '.[dev]'`, then run `ruff check src scripts tests` and `ruff format --check src scripts tests`. The notebook's code cells were also executed successfully during final QA.

Raw downloads, local profiles, databases, training JSONL, model weights/adapters, measurements, audit replay exports, virtual environments and credentials are ignored. Only small synthetic fixtures are committed. Detailed working notes are in a private Obsidian vault outside Git. The only human-facing documentation files are this README and the required technical-spike summary.

## Known limitations

- Qwen still failed exact semantics on 27/45 supported runs. Wrong horizons, incomplete evidence requirements, extra filters/keys and incorrect intents were rejected or handled by labeled fallback. CUDA fine-tuning remains unmeasured.
- Semantic grounding deliberately uses a full-request grammar and a fixed catalog. Unknown paraphrases and additional qualifiers abstain, even if a model could interpret them. Forecasts require both product and warehouse and use 14 days; what-if supports explicit demand increases only, not supplier-delay scenarios.
- Risk calibration and forecast evaluation use synthetic distributions and do not establish real operational validity. Forecast quantile bounds under-covered in the measured spike; no confidence percentage is shown.
- SQL deliberately excludes CTEs, nested queries, arbitrary functions, cross joins and noncatalog joins. Model SQL is never accepted; only compiled, AST-validated templates execute.
- Fixed dates, small entity counts, no authenticated reviewer identity, no concurrent-user workflow, and no external audit anchor. Local database owners can rewrite the database; the hash chain detects accidental edits, not a fully privileged adversary.
- Repeated spike inputs are smoke tests. Timings include process startup and depend on hardware, interpreter and imports. Sparse history, invalid dates, stale stock and missing fields require review.

Next model work: expand curated QueryPlan instructions and a genuinely unseen evaluation set before deciding whether a CUDA QLoRA experiment is worthwhile. Keep deterministic fallback enabled. Forecast interval coverage also remains a limitation. Presentation and visual-design work remain a separate phase.
