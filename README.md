# Sentinel

Sentinel is a human-approved, read-only supply-chain operations console for CMPS 4200 HCI. The demo uses independently generated synthetic data. It supports bounded natural-language SQL questions, operational risk review, demand forecasts and what-if analysis, evidence-backed recommendations, simulated human decisions, and audit replay.

## Current Phase 2 status

The executable coding work was implemented on `phase2-coding`: dataset research tooling, 14 canonical DuckDB tables, four semantic views, 11 failure fixtures, a constrained SQL-free planner interface, SQL safety, calibrated synthetic risk models, forecasting, recommendations, a command-line review gate, audit replay, training scripts, and reproducible evaluation. That branch retains its 376-test baseline. The separate `semantic-repair` branch passes 461 tests, adding 85 regressions and integrity checks without changing existing tests. Its versioned execution contract and date/supplier repair preserve the compiler, AST validation, evidence and approval protections.

Eleven core Kaggle datasets were downloaded and profiled locally. The backorder competition returned `UnauthenticatedError`; nine benchmark competitions are registered but unattempted. No Kaggle records enter the executable demo or training examples.

The measured spike used deterministic planning and lexical retrieval: 36/36 expected behaviors, 6/6 unsafe cases blocked, 24/24 correct abstentions. SQL execution completed in 18/21 cases; the other three were intentional timeouts. These are smoke-test counts, not general accuracy claims. [Technical-spike findings](docs/technical_spike.md) include measured latency and limitations.

Qwen and BGE were genuinely evaluated on this host's Apple M4 Max GPU. Qwen proposes SQL-free `QueryPlan` objects, never executable SQL. On the original 24-case development set repeated three times, 36/72 model outputs passed the plan schema and per-intent constraints; 24/72 exactly matched intended semantics, including 18/45 supported runs. Those development results do not generalize to the 48-case frozen comparison below: its zero-shot and experimental grounded-prompt arms both remain at 0/30 supported exact matches. Development-grounded parser changes improved rules and paired fallback from 8/30 to 14/30, but coverage remains limited. All negative cases abstained safely. The model is **not ready** as an independent planner, and the experimental prompt was not promoted. QLoRA has not run. The instruction set contains 141 SQL-free examples.

## Semantic-repair branch

### Robust-semantic-repair development

`robust-semantic-repair` branches from `d7907bc`, preserving both earlier branches. It separates candidate intent discovery, registry-backed slots, five dedicated planners, and typed execution validation. Missing entities, dates and scenario quantities now produce audited clarification details. Qwen remains shadow-only; schema echoes, malformed outputs and semantic disagreements are recorded separately. SQL compilation, AST checks, evidence and review authority are unchanged.

The versioned internal instruction generator retains 141 prior core labels and adds 36 reviewed development examples. It emits 177 SQL-free V2 targets in family-grouped train/validation/development splits (131/32/14). Alias/number-normalized lexical near-duplicates cannot cross these splits, and messages contain no shared few-shot questions. This guard is not proof against all semantic overlap. None of these splits is an independent benchmark. The new V2 files are preparation data, intentionally **not accepted by the existing V1 QLoRA trainer**; training and runtime-adapter promotion require a separately reviewed contract migration and external evaluation.

```sh
python -m scripts.robust_instruction_data --output data/training/robust_v1
python -m scripts.blinded_benchmark predict --questions /path/to/questions.json --sha256 QUESTIONS_SHA256 --output artifacts/blind_run
# Keep the printed prediction digest separately; only then obtain the labels.
python -m scripts.blinded_benchmark score --predictions artifacts/blind_run/predictions.json --predictions-sha256 PREDICTIONS_SHA256 --labels /path/to/labels.json --labels-sha256 LABELS_SHA256
```

Use fresh output directories. The question manifest has `protocol: "sentinel_blind_questions_v1"`, a `benchmark_id`, `reference_date: "2026-09-30"`, `provenance` (`internal` or `externally_authored_claimed`), and `cases` containing only `id` and `question`. Labels use `protocol: "sentinel_blind_labels_v1"`, the same `benchmark_id`, `questions_sha256`, and cases containing `id`, `category`, and a complete canonical V2 `expected` plan. The isolated prediction worker receives questions only. Scoring preserves legacy field/exact-match diagnostics and reports V2 exact match and metadata fields separately. Hashes detect changes relative to pinned digests; they do not authenticate authorship. No external benchmark has been supplied, so independent promotion remains blocked.

The following measurements describe the earlier `semantic-repair` milestone, not a new independent evaluation:

`semantic-repair` starts from the preserved 376-test tip `89f4064`, which includes `e759d7c`; it does not change `phase2-coding`. V1 SQL-free proposals and original evaluation labels remain compatible. Before compilation, the application creates and validates a V2 execution contract containing explicit granularity, date basis, supplier scope and abstention reason. Qwen is shadow-only: even an agreeing proposal leaves execution ownership with the deterministic planner, and disagreements remain in the audit record.

Calendar normalization uses the fixed demo date, not the host clock. Internal intervals are always `[start, end)`. Natural-language explicit ranges include their final day unless marked exclusive; “past N days” means N completed days before the reference date. Last/this/next month, previous quarter and explicit dates normalize deterministically. Future historical queries abstain. Daily/weekly history granularity is retained by the date resolver, but those breakdowns currently abstain in the supplier aggregate template; daily 14-day demand forecasting is unchanged. Supplier lateness can filter one canonical supplier or explicitly represent all suppliers, using promised dates and evaluable observations, never invented risk probabilities.

The registry resolves exact IDs, known aliases, then lexically evidenced catalog candidates. Embedding proximity alone cannot identify an unknown supplier or warehouse. BGE schema retrieval remains unchanged.

```sh
python -m scripts.evaluate_semantic_repair --development --output artifacts/repair_development
# Compare a trusted detached baseline checkout, without changing the active branch:
python -m scripts.evaluate_semantic_repair --development --source-root /path/to/baseline-checkout --output artifacts/repair_baseline
```

The worker receives questions only; expected plans stay in the scoring process. Use fresh output directories. This evaluation reports whole-plan and field matches separately, negative behavior, evidence linkage, audit verification and actual request latency. No fine-tuning runs.

Development froze in `5a82962` before the new 39-case benchmark was created and checksum-frozen in `73e60ea`. It contains six supplier-lateness cases and four each for stockout, daily forecasting, supplier evidence and what-if (22 supported), plus four missing-field, four ambiguous/unsupported-granularity, three unsupported and six unsafe cases. Questions are disjoint from existing development questions and the original holdout. This is a post-development set from the same implementation author, not an independent blinded benchmark. No parser or prompt changes followed either final evaluation.

```sh
python -m scripts.evaluate_semantic_repair --benchmark data/sample/semantic_benchmark_v2.json --sha256 8185221a88cb0fa8908ef7c62ce2964f20cf0041e7c5abcf2ce4b2593fcb3c38 --output artifacts/repair_unseen
# The final original-holdout run used this command once, after development:
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 python -m scripts.compare_query_plans --repeats 1 --output artifacts/semantic_repair_original_holdout
```

New benchmark, deterministic baseline `89f4064` → repair:

| Measurement | Before → after |
| --- | --- |
| Whole-plan exact match | 32/39 → 36/39 |
| Supported exact match and successful answers | 15/22 → 19/22 (68.18% → 86.36%) |
| Supplier-lateness match | 2/6 → 5/6 |
| Stockout match | 3/4 → 4/4 |
| Forecast / supplier evidence / what-if | Unchanged: 3/4 / 4/4 / 3/4 |
| Applicable date fields | 2/6 → 5/6 |
| Applicable entities | 10/14 → 11/14 |
| Metrics, grouping, evidence requirements (each) | 15/22 → 19/22 |
| Applicable scenario / horizon | 3/4 → 3/4; 6/8 → 7/8 |
| Intent / abstention label (each) | 32/39 → 36/39 |
| Negative-plan match and safe abstention | 17/17 → 17/17 |
| Unsafe rejection | 6/6 → 6/6 |
| Verified evidence linkage | 15/15 → 19/19 |
| Verified audit events | 54 → 58 |

These are deterministic results, not Qwen results. New-benchmark min/mean/median/p95/max latency was 13.85/169.95/28.22/430.19/496.15 ms before and 15.29/205.72/39.43/438.34/478.70 ms after. The repair completes four more analyses instead of abstaining. The three remaining supported misses abstained. Date resolver tests cover last/next/this month, past 30 completed days, previous quarter, daily/weekly history, explicit boundaries, ambiguity, missing dates, leap dates and year rollover. Development date/filter variations improved from 1/6 to 6/6 supported matches, retaining 4/4 correct negative plans. The original 141 instruction labels remain 141/141; neither figure is unseen accuracy.

The existing overlapping taxonomy and field definitions were retained unchanged. On the saved 72-call real-model development report, diagnostic counts include 27 wrong intents, 36 wrong entities, three wrong dates, 24 wrong metrics, 24 wrong groupings, nine missing evidence requirements, three incorrect abstentions and three unsupported proposals incorrectly accepted by the proposal schema (not executed). The development date/filter baseline additionally exposed one missing filter and five incorrect abstentions; all ten development variants match after repair. Missing scenario fields, malformed outputs and unsupported acceptance remain explicitly classified and regression-tested.

The original 48-case holdout was run once after development with genuine Qwen/BGE inference: **no accuracy change** versus `89f4064`. Rules and paired fallback remain 31/48 whole-plan, 14/30 supported, 17/18 negative-plan match, with supplier lateness still 0/6. Zero-shot Qwen remains 0/48 valid; grounded Qwen remains 28/48 valid, 6/48 exact and 0/30 supported. Fallback remains 31/48. All arms safely abstained on 18/18 negatives and blocked 6/6 unsafe requests. All 220 audit events and 28/28 recommendation-bearing evidence snapshots verified. Both original and new checksums remained unchanged; earlier-session exposure still limits the original benchmark's independence.

Original-holdout latency min/mean/median/p95/max in milliseconds: zero-shot 2,215.66/6,987.17/9,975.72/10,159.65/10,192.09; grounded 2,124.48/2,598.94/2,274.65/3,307.82/5,011.21; rules 14.57/392.53/66.89/1,234.50/1,407.58; paired fallback 2,092.43/2,889.92/2,803.65/4,187.64/4,980.92. Model initialization was 5,947.68 ms, peak process RSS 6.34 GiB, and 96 real generations completed without exceptions on `mps:0`. Model revisions were unchanged. Qwen was not evaluated on the new 39-case benchmark; no model improvement is claimed. The earlier BGE 45/45 top-three figure remains a development-set measurement, not a new benchmark result.

The measured repair passes regression, safety, evidence and audit checks and improves the new benchmark, but remains a limited experimental branch: the original supplier-lateness gap is unresolved and some paraphrases still abstain. Qwen stays shadow-only, fallback stays enabled, and QLoRA remains deferred—not demonstrated as a product fix. An independent new benchmark and broader development-only coverage are needed before promotion or a separately authorized SQL-free instruction-tuning experiment.

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

### Frozen pretraining comparison

The 48-case `data/sample/query_plan_heldout_v1.json` was frozen in commit `b9a65b2` before the instruction expansion or new prompt implementation. Its SHA-256 is checked by the loader. It contains six requests for each of supplier late-delivery rates, stockout coverage, daily forecasting, supplier evidence and demand what-if (30 supported semantic targets), plus six missing-field, three ambiguous, three unsupported and six unsafe requests. Cases carry canonical plans, required fields, abstention labels and evidence requirements. Aliases, date boundaries and multi-filter forecasts are included. Targets are manually specified independently of the planner. Individual cases were inspected in earlier sessions, so subsequent comparisons must not be described as perfectly untouched benchmarks.

```sh
python -m scripts.build_sft_dataset --version curated-v2
python -m scripts.compare_query_plans --rules-only --output artifacts/heldout_rules
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 python -m scripts.compare_query_plans --repeats 1 --output artifacts/heldout_comparison
```

Use a fresh output directory; existing evaluation databases are never overwritten. The comparison uses the same unmodified Qwen Instruct weights for a zero-shot contract/catalog arm and a detailed-prompt + four training-only examples + BGE arm. The deterministic arm uses the current bounded parser. The fallback arm replays the exact grounded generation through the product gate with fallback enabled, isolating the effect of fallback without generating a different answer. No gold labels enter prompts or grant execution authority. The detailed prompt is experimental and is not installed in the production console.

Model-only semantic scores compare canonical proposals with independent labels **before** production phrase grounding. Product acceptance and selected fallback-plan scores are separate: unfamiliar but representable requests can receive correct model proposals and still abstain through the unchanged safety gate. Invalid JSON gets no correct-negative-plan credit merely for being rejected. Unsafe-request rejection measures the complete guard, not model compliance. Reports include per-category numerators/denominators, raw generations, prompts, retrieval, source hashes, memory and latency. Fallback latency includes the observed shared inference cost plus separately measured replay; it is not an independent model call. One greedy pass measures 48 unique requests per arm, not a statistical generalization guarantee. Do not tune against these revealed results; freeze another unseen set for future iteration.

Historical comparison at `e759d7c`: 96 real generations (48 per prompt), 48 rule requests and 48 paired fallback replays; no generation exception or fixture substitution. Model revisions were unchanged from the earlier spike. Initialization took 8,413.12 ms; peak process RSS was 5.13 GiB (not GPU-only memory). All 208 audit events verified.

| Arm | Valid plans | Exact semantic match | Supported exact match | Correct negative plan | Fallback | Mean latency ms |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen zero-shot contract | 0/48 | 0/48 | 0/30 | 0/18 | 0/48 | 7,038.86 |
| Qwen experimental prompt + BGE | 28/48 | 6/48 | 0/30 | 6/18 | 0/48 | 2,608.98 |
| Deterministic rules | 48/48 | 25/48 | 8/30 | 17/18 | 0/48 | 225.55 |
| Grounded output + paired fallback | 48/48 | 25/48 | 8/30 | 17/18 | 27/48 | 2,764.84 |

All four arms safely abstained on 18/18 negative requests and rejected 6/6 unsafe requests without execution. The rules labeled one destructive paraphrase `unsupported` rather than `unsafe`, so it receives no exact-plan credit even though it was blocked. Both model arms scored 0/6 on each supported intent. Rules/fallback scored 0/6 supplier-delay, 2/6 stockout, 2/6 forecasting, 2/6 supplier-evidence and 2/6 what-if. Grounded exact negatives were 3/6 missing-field, 2/3 ambiguous, 1/3 unsupported and 0/6 unsafe. Rules/fallback were 6/6, 3/3, 3/3 and 5/6 respectively. The primary fallback scores describe selected application plans, not Qwen quality.

Latency min/median/p95/max in milliseconds: zero-shot 2,180.13 / 10,046.32 / 10,357.63 / 10,456.73; grounded 2,122.68 / 2,288.75 / 3,379.05 / 5,052.79; rules 14.52 / 43.69 / 1,174.29 / 1,238.24; paired fallback 2,123.03 / 2,291.07 / 3,915.02 / 5,013.82. These single-host timings include abstentions; early calls shared the host with regression checks. Prompt detail, examples and retrieval change together, so this is not an isolated BGE ablation. The zero-shot arm frequently echoed schema metadata or emitted incomplete fenced output; the grounded arm frequently abstained incorrectly or supplied wrong/missing fields. Do not promote this prompt or infer that fine-tuning will fix it.

The expanded instruction set contains 141 examples (93 train, 24 validation, 24 test): the retained 41-example pilot plus 100 individually authored additions. By curation category: supplier delay 11, stockout 17, forecast 13, supplier evidence 13, what-if 16, shipment evidence 3, missing fields 10, ambiguous 8, unsupported 23, unsafe 15, and malformed-output recovery 12. Recovery examples treat previous output as untrusted and regenerate only from supplied facts; incomplete requests abstain. They do not enable automatic repair in production. Targets are canonical SQL-free JSON; curated labels are checked independently of the narrow runtime grammar. Exact normalized holdout questions are excluded from targets and few-shot context. Source seeds stay in the generator code; generated JSONL stays ignored in `data/training/query_plan_v2`.

### Development diagnostics and semantic coverage

```sh
python -m scripts.evaluate_development --output artifacts/dev_snapshot.json
# After a future development change, preserve those labels for comparison:
python -m scripts.evaluate_development --baseline artifacts/dev_snapshot.json --output artifacts/dev_after.json
```

This command loads only existing instruction/development cases, not the frozen holdout. Optional `--model-report PATH` diagnoses a saved real-model development report after verifying every question belongs to the existing development inventory. Reports and intermediate snapshots remain ignored. The semantic parser was developed against the 141 instruction cases: exact match improved from 87/141 to 141/141, including supported requests from 32/82 to 82/82. This is development fit, not unseen accuracy. Labels were captured before parser edits and reused for the after measurement.

Additive diagnostics retain whole-plan exact match and report intent, canonical entities, date range, metrics, grouping, scenario, evidence, abstention and horizon matches. Scores include all cases and a separate applicable-field denominator to expose easy null-field matches. Missing fields never receive null-field credit. Safe fence removal, canonical aliases and order-independent metric lists use the existing contract conventions; duplicate fields/items and contract violations remain invalid. Partial field credit cannot authorize execution or count as whole-plan success.

The overlapping error taxonomy covers wrong intent, entity normalization, missing filters, date range, metric, grouping, evidence, scenario parameters, incorrect abstention, unsupported acceptance and formatting, plus explicit contract violations. Unsupported acceptance describes a valid **proposal**, not a database action. On the prior 72-call real-model development report, exact match remained 24/72; observed errors included 36 entity mismatches, 27 wrong intents, 24 metric mismatches, 24 grouping mismatches and nine missing-evidence cases. No new training or model-quality improvement is implied by these diagnostics.

The deterministic parser consumes canonical entity slots, number words, monthly/quarterly and bounded explicit dates, 14-day daily forecast horizons, inventory-coverage thresholds and explicit demand increases/multipliers. Unknown vocabulary, contradictory quantities, extra filters, negation and unsupported qualifiers abstain. Only existing allowlisted plans can be compiled. Tests cover every curated development case and adversarial modifications; two older tests now assert the invariant rather than requiring a particular held-out question to remain unrecognized. Qwen remains opt-in and experimental, with fallback enabled.

### Final comparison after development freeze

Development was committed as `afa0fc0` before one final 48-case pass, using unchanged prompts, model revisions and whole-plan scoring definitions:

```sh
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 python -m scripts.compare_query_plans --repeats 1 --output artifacts/heldout_semantics_final_v1
```

The holdout checksum remained `a5c51259c8d0a34ae667a208ad67a003fb921fbfebfbedf9dc7bd63bfd1e47dc`. No parser changes followed this evaluation. Earlier-session exposure to individual cases remains a benchmark limitation; this is not a perfectly untouched holdout.

| Arm | Valid plans | Exact match, before → after | Supported match, before → after | Correct negative plan | Fallback, before → after |
| --- | --- | --- | --- | --- | --- |
| Qwen zero-shot | 0/48 | 0 → 0/48 | 0 → 0/30 | 0/18 | 0 → 0/48 |
| Qwen grounded + BGE | 28/48 | 6 → 6/48 | 0 → 0/30 | 6/18 | 0 → 0/48 |
| Deterministic | 48/48 | 25 → 31/48 | 8 → 14/30 | 17/18 | 0 → 0/48 |
| Grounded + paired fallback | 48/48 | 25 → 31/48 | 8 → 14/30 | 17/18 | 27 → 31/48 |

Supported exact match improved from 26.67% to 46.67% for rules/fallback. By intent, before → after out of six: supplier lateness 0 → 0, stockout 2 → 3, forecasting 2 → 5, supplier evidence 2 → 3, what-if 2 → 3. Qwen remains 0/6 in every supported category. All four arms safely abstained on 18/18 negative requests and rejected 6/6 unsafe requests without query execution. Rules/fallback still classify one unsafe paraphrase as unsupported: safe behavior, but no exact-label credit. Final system abstention was 34/48 for rules/fallback and 48/48 for model-only arms; 14 supported application results succeeded with validated plans.

Field scores below use applicable/non-null target fields, except intent and abstention which cover all 48. Rules and selected fallback plans have identical scores. Model proposals are scored separately, including partial fields in rejected outputs; partial credit never authorizes execution. Zero-shot receives zero field credit. Retrospective field diagnostics on the saved `e759d7c` outputs use the same additive definitions.

| Field | Rules/fallback before → after | Grounded Qwen, unchanged |
| --- | --- | --- |
| Intent | 25 → 31/48 | 17/48 |
| Entities | 6 → 11/18 | 5/18 |
| Time range | 0 → 0/6 | 2/6 |
| Metrics | 8 → 14/30 | 12/30 |
| Grouping | 8 → 14/30 | 11/30 |
| Scenario | 2 → 3/6 | 0/6 |
| Evidence requirements | 8 → 14/30 | 6/30 |
| Abstention label | 26 → 32/48 | 22/48 |
| Horizon | 4 → 8/12 | 3/12 |

Actual request latency in milliseconds, including abstentions and successful analytics:

| Arm | Minimum | Mean before → after | Median | p95 | Maximum |
| --- | --- | --- | --- | --- | --- |
| Zero-shot | 2,248.24 | 7,038.86 → 7,015.37 | 10,063.13 | 10,194.22 | 10,339.95 |
| Grounded | 2,161.27 | 2,608.98 → 2,617.61 | 2,288.32 | 3,334.94 | 5,046.43 |
| Rules | 14.74 | 225.55 → 392.57 | 67.17 | 1,226.42 | 1,364.48 |
| Paired fallback | 2,138.70 | 2,764.84 → 2,905.58 | 2,812.66 | 4,267.12 | 5,001.75 |

More rules/fallback requests now execute analytics instead of abstaining, so their higher mean is not an isolated parser-speed regression. This run made 96 genuine generations with no generation exceptions, plus 48 deterministic requests and 48 paired replays; 220 audit events verified. Initialization took 8,014.20 ms; peak process RSS was 5.36 GiB, not GPU-only memory. Both models loaded on `mps:0`. Regression tests ran afterward, not concurrently. The historical BGE 45/45 top-three result is from the earlier development set, not a new retrieval-quality claim for this holdout.

QLoRA is not yet justified as a product fix: residual deterministic coverage and conservative request grounding remain architectural limits, while independent Qwen semantic accuracy is still zero here. The taxonomy identifies candidate learnable errors, but does not establish that training would fix them. Keep Qwen experimental and fallback enabled. Future development must use development cases and a newly frozen unseen evaluation, not these revealed holdout labels. Any separately authorized training must target SQL-free QueryPlan JSON and demonstrate improved semantics without safety or latency regressions.

No QLoRA training is authorized by these evaluation commands. If separately approved later, the existing recipe requires a compatible NVIDIA CUDA environment:

```sh
python -m scripts.build_sft_dataset --version curated-v2
python -m scripts.train_qlora --data data/training/query_plan_v2 --epochs 1 --max-length 4096
python -m sentinel ask 'Why is Supplier A considered high risk?' --backend qwen --adapter models/sentinel-qlora
```

The original `python scripts/build_sft_dataset.py` command remains available for the 41-example pilot in `data/training/query_plan_v1`. No raw Kaggle rows or SQL targets are used in either version. The training preflight rejects legacy contracts, invalid plans, altered curated labels and frozen-holdout leakage. Target questions are disjoint across splits, but schema, entity vocabulary, templates and shared training-only few-shot context overlap; these are not entity-held-out splits.

Training uses PEFT LoRA over a 4-bit NF4 base model, fixed seeds and prompt-masked targets. Examples exceeding the context budget fail explicitly. The script writes actual training/evaluation metrics only after execution. Validation loss is not planning accuracy. Hosts without CUDA exit with a clear not-run reason. No adapter was trained or evaluated in this pass. A future adapter must beat the base model on preserved semantic labels without regressing latency or safety; a completed training job alone is not evidence of usefulness. The runtime grammar remains an independent limit that fine-tuning cannot remove.

## Verification and intentionally uncommitted files

`python -m pytest -q` covers profiles, all canonical tables, scenarios, planner JSON, destructive and unknown-column queries, SQL bypass attempts, timeouts, empty/missing data, risk schemas, forecast intervals, evidence integrity, review decisions, audit tampering/replay, training splits, the CLI and the complete spike. `python scripts/quality_gate.py` checks staged content, credentials, file sizes and author identity before a milestone commit.

For code-style checks, install `python -m pip install -e '.[dev]'`, then run `ruff check src scripts tests` and `ruff format --check src scripts tests`. The notebook's code cells were also executed successfully during final QA.

Raw downloads, local profiles, databases, training JSONL, model weights/adapters, measurements, audit replay exports, virtual environments and credentials are ignored. Only small synthetic fixtures are committed. Detailed working notes are in a private Obsidian vault outside Git. The only human-facing documentation files are this README and the required technical-spike summary.

## Known limitations

- The frozen comparison found no supported exact matches for either experimental model arm. This is a different evaluation and prompt from the earlier 40% development result, not a controlled before/after accuracy comparison. CUDA fine-tuning remains unmeasured.
- Semantic grounding deliberately uses a full-request grammar and a fixed catalog. Unknown paraphrases and additional qualifiers abstain, even if a model could interpret them. Forecasts require both product and warehouse and use 14 days; what-if supports explicit demand increases only, not supplier-delay scenarios.
- Risk calibration and forecast evaluation use synthetic distributions and do not establish real operational validity. Forecast quantile bounds under-covered in the measured spike; no confidence percentage is shown.
- SQL deliberately excludes CTEs, nested queries, arbitrary functions, cross joins and noncatalog joins. Model SQL is never accepted; only compiled, AST-validated templates execute.
- Fixed dates, small entity counts, no authenticated reviewer identity, no concurrent-user workflow, and no external audit anchor. Local database owners can rewrite the database; the hash chain detects accidental edits, not a fully privileged adversary.
- Repeated spike inputs are smoke tests. Timings include process startup and depend on hardware, interpreter and imports. Sparse history, invalid dates, stale stock and missing fields require review.

Next model work: review the frozen comparison and the separate runtime-coverage limitation before proposing any training. Preserve this evaluation set; use a new unseen set for prompt iteration. Keep deterministic fallback enabled, and reject any future adapter that fails the semantic, latency or safety comparison. Forecast interval coverage also remains a limitation. Presentation and visual-design work remain a separate phase.
