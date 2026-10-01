# Sentinel

Sentinel is a human-approved, read-only supply-chain operations console for CMPS 4200 HCI. The demo uses independently generated synthetic data. It supports bounded natural-language SQL questions, operational risk review, demand forecasts and what-if analysis, evidence-backed recommendations, simulated human decisions, and audit replay.

## Current Phase 2 status

The executable coding work is implemented on `phase2-coding`: dataset research tooling, 14 canonical DuckDB tables, four semantic views, 11 failure fixtures, a validated planner interface, SQL safety, calibrated synthetic risk models, forecasting, recommendations, a command-line review gate, audit replay, training scripts, and a reproducible technical spike. The current suite passes 121 tests.

Eleven core Kaggle datasets were downloaded and profiled locally. The backorder competition returned `UnauthenticatedError`; nine benchmark competitions are registered but unattempted. No Kaggle records enter the executable demo or training examples.

The measured spike used deterministic planning and lexical retrieval: 36/36 expected behaviors, 6/6 unsafe cases blocked, 24/24 correct abstentions. SQL execution completed in 18/21 cases; the other three were intentional timeouts. These are smoke-test counts, not general accuracy claims. [Technical-spike findings](docs/technical_spike.md) include measured latency and limitations.

Qwen and BGE were provisioned and genuinely executed on this macOS arm64 host's Apple M4 Max GPU. The unchanged base planner failed the strict JSON contract on all 21 measured generations because it emitted Markdown-fenced JSON. All became audited abstentions; none of its SQL executed and no deterministic fallback was substituted. Static inspection also found incorrect metrics, entity filters and missing analysis inputs. The base-model path is not ready for successful end-to-end use. QLoRA was not run because the existing recipe requires CUDA. Training examples were previously generated and validated (360 training, 90 validation, 90 test); no fine-tuning result is claimed.

## Scope

All purchasing, shipment changes, supplier changes and follow-up actions are simulations. Approval records a local decision only. Business tables remain read-only through the question interface; trusted setup creates the synthetic database and the application appends audit events.

Excluded: real company data claims, healthcare data, external actions, autonomous operations, generic chat, voice, multi-model orchestration, and training an LLM from scratch. Presentation, PowerPoint, wireframes, user flows, and information architecture are outside this coding pass.

## Architecture and model stack

The request path is: question → relevant schema and metrics → JSON plan → AST SQL checks → isolated read-only DuckDB query → verified evidence → deterministic recommendation → human simulation gate → audit snapshot.

| Component | Implementation |
| --- | --- |
| Planner | `Qwen/Qwen2.5-Coder-1.5B-Instruct` interface; explicitly labeled deterministic rules for offline execution |
| Retrieval | `BAAI/bge-small-en-v1.5` interface; lexical TF-IDF baseline for offline execution |
| SQL safety | SQLGlot AST and column validation, four-view allowlist, documented equality joins, named parameters, maximum 200 rows, three-second worker deadline |
| Execution | Read-only DuckDB; external access and automatic extension loading disabled; worker termination on timeout |
| Risk | Logistic regression with sigmoid calibration on separate synthetic observations, plus coverage, traffic, delay-burst and data-quality rules |
| Forecasting | Seven-day seasonal naive; 0.1/0.5/0.9 quantile `HistGradientBoostingRegressor`; chronological walk-forward evaluation |
| Recommendations | Verified SQL snapshots, model/rule sources, assumptions, missing information and human review; no LLM-authored actions |
| Audit | Hash-linked local events and immutable snapshot replay through the application; named reviewer for simulated decisions |

Canonical tables: `orders`, `order_items`, `shipments`, `products`, `suppliers`, `warehouses`, `inventory_daily`, `demand_daily`, `purchase_orders`, `supplier_events`, `promotions`, `calendar`, `risk_events`, `audit_events`. The four permitted query views are `risk_view`, `demand_view`, `supplier_view`, and `shipment_view`. Every base table includes source, record, synthetic entity, quality, missing-field count and timestamp provenance.

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

The offline planner recognizes these forms, shipment queries such as `Show shipments for Supplier Z`, and `Show shipments missing promised delivery dates`. Other forms clarify or abstain. A stockout result means low estimated inventory coverage, not a guaranteed future stockout.

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
python -m sentinel ask 'Why is Supplier A considered high risk?' --backend qwen --retrieval bge
python scripts/run_technical_spike.py --backend qwen --retrieval bge --output artifacts/model_spike
```

The declared `models` extra installed successfully with Python 3.14.3 on macOS 15.6. The tested versions were `torch==2.14.1`, `transformers==4.57.6`, `sentence-transformers==5.7.0`, `accelerate==1.15.0`, `peft==0.21.1`, `datasets==5.0.1`, `tokenizers==0.22.2`, `huggingface-hub==0.36.2`, and `safetensors==0.8.0`. The extra uses version ranges, so a later installation may resolve differently. The Linux-only bitsandbytes dependency was not installed on macOS.

For Apple Silicon inference, check Metal access from the terminal used to run the model:

```sh
python -c 'import torch; print("MPS:", torch.backends.mps.is_available(), "CUDA:", torch.cuda.is_available())'
```

A restricted process may report MPS unavailable even when the host supports it. The measured run used a normal GPU-enabled process: both models loaded on `mps:0`, with Qwen in bfloat16. Weights stayed in the default Hugging Face cache, outside this repository (approximately 2.9 GiB for Qwen and 128 MiB for BGE). No API key was required. Qwen revision: `2e1fd397ee46e1388853d2af2c993145b0f1098a`; BGE revision: `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`.

The base-model measurement used the five supported example requests (including forecasting), plus unsupported and destructive requests, repeated three times. End-to-end latency including retrieval, generation, validation and audit was min 1,468.04 ms, mean 2,459.57 ms, median 2,500.53 ms, p95 3,263.43 ms, max 5,215.74 ms; these are abstention latencies, not successful-answer timings. Structured validity was 0/21 and supported SQL acceptance was 0/15. BGE retrieved the expected view first in 12/15 supported calls and within its top three in 15/15; repetitions are not independent accuracy samples. All three destructive requests were stopped at JSON validation. Separate static inspection confirmed the generated DELETE was also rejected by the SQL guard, without executing it. An explicitly injected unavailable-model case also produced an audited abstention. The local measurement artifacts are intentionally not committed.

Runtime defaults to local-only model loading. Missing dependencies or weights produce an unavailable result; a requested Qwen run is never relabeled as successful model inference through a rules fallback. The explicit failure-injection cases still use their test doubles.

QLoRA requires a compatible NVIDIA CUDA environment for this recipe:

```sh
python scripts/build_sft_dataset.py
python scripts/train_qlora.py --epochs 1 --max-length 4096
python -m sentinel ask 'Why is Supplier A considered high risk?' --backend qwen --adapter models/sentinel-qlora
```

Training uses PEFT LoRA over a 4-bit NF4 base model, fixed seeds, prompt-masked targets, and separate entity groups for train/validation/test. Generated records are JSON/SQL-validated. Examples exceeding the context budget fail explicitly. The script writes actual training/evaluation metrics only after execution. Template overlap across splits remains a generalization limitation; validation loss is not SQL accuracy. CPU-only hosts exit with a clear not-run reason.

## Verification and intentionally uncommitted files

`python -m pytest -q` covers profiles, all canonical tables, scenarios, planner JSON, destructive and unknown-column queries, SQL bypass attempts, timeouts, empty/missing data, risk schemas, forecast intervals, evidence integrity, review decisions, audit tampering/replay, training splits, the CLI and the complete spike. `python scripts/quality_gate.py` checks staged content, credentials, file sizes and author identity before a milestone commit.

For code-style checks, install `python -m pip install -e '.[dev]'`, then run `ruff check src scripts tests` and `ruff format --check src scripts tests`. The notebook's code cells were also executed successfully during final QA.

Raw downloads, local profiles, databases, training JSONL, model weights/adapters, measurements, audit replay exports, virtual environments and credentials are ignored. Only small synthetic fixtures are committed. Detailed working notes are in a private Obsidian vault outside Git. The only human-facing documentation files are this README and the required technical-spike summary.

## Known limitations

- Qwen/BGE inference is measured, but the unchanged base planner returned no accepted answers in this sample. Markdown fences violate the raw JSON contract, and removing them alone would not resolve the observed semantic errors. CUDA fine-tuning remains unmeasured.
- Risk calibration and forecast evaluation use synthetic distributions and do not establish real operational validity. Forecast quantile bounds under-covered in the measured spike; no confidence percentage is shown.
- SQL deliberately excludes CTEs, nested queries, arbitrary functions, cross joins and noncatalog joins. Model output may be rejected even when syntactically valid SQL.
- Fixed dates, small entity counts, no authenticated reviewer identity, no concurrent-user workflow, and no external audit anchor. Local database owners can rewrite the database; the hash chain detects accidental edits, not a fully privileged adversary.
- Repeated spike inputs are smoke tests. Timings include process startup and depend on hardware, interpreter and imports. Sparse history, invalid dates, stale stock and missing fields require review.

Next model work, separately scoped: address the observed structured-output and semantic-planning failures, then repeat base-model evaluation on unseen paraphrases before attempting fine-tuning. Forecast interval coverage also remains a limitation. Presentation and visual-design work remain a separate phase.
