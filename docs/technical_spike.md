# Phase 2 technical spike

Measured on 2026-09-30, macOS arm64, Python 3.14.3. Run `python scripts/run_technical_spike.py` to reproduce the experiment. Full per-call records and audit snapshots are written to ignored `artifacts/technical_spike/`. The notebook runs the same script.

## Scope and implementation

The measured run used **deterministic planning and lexical schema retrieval** on independent synthetic data, seed 4200, with a fixed analysis date of 2026-09-30. Qwen and BGE interfaces are implemented but their weights were not available locally. QLoRA was not run: this host has no CUDA and the optional torch dependency is not installed. No language-model quality, latency or fine-tuning result is claimed.

Questions pass through schema retrieval, a JSON-schema contract, an AST SQL guard and a read-only DuckDB worker. The guard accepts a limited SELECT language over four approved views, validates columns and documented joins, binds parameters and caps rows at 200. A separate process enforces the query deadline. External database access and extension loading are disabled. JSON errors, SQL violations, unavailable models, incomplete evidence and empty results produce explicit abstention or clarification.

Recommendations use verified query snapshots, risk outputs, forecasts and deterministic templates. The application records pending simulated actions. A named reviewer can edit, approve or reject; edits require another approval. Approval changes only the simulation record. Audit replay verifies the hash chain and returns recorded snapshots without executing SQL or actions.

## Actual results

Twelve cases ran three times, for 36 calls. All 36 matched their expected behavior. Latencies include retrieval, validation, worker startup and SQL execution; they exclude model initialization, risk fitting, forecasting and audit writes. Very fast blocked calls are included, so this mixture is not an estimate of normal query latency.

| Measurement | Observed |
| --- | ---: |
| Minimum | 0.359 ms |
| Average | 475.478 ms |
| Maximum | 1094.167 ms |
| Median | 452.969 ms |
| p95 (36 calls) | 985.241 ms |
| Execution success | 18/21 (85.71%) |
| Unsafe-query blocking | 6/6 (100%) |
| Correct abstention | 24/24 (100%) |

Execution success counts completed SQL calls, including empty and missing-field results, over all SQL calls that passed validation. Its three failures are intentionally timed-out calls. Unsafe blocking covers both the language-level destructive request and an injected destructive planner SQL output. Correct abstention requires both an abstention flag and the expected failure/empty status. These are repeated smoke-test counts, not estimates of general safety or accuracy.

The first repetition produced:

| Input / case | Evidence and outcome | Latency |
| --- | --- | ---: |
| Which suppliers had the highest late-delivery rate last month? | `shipment_view`; grouped evaluable late deliveries using promised dates in August 2026; executed | 952.177 ms |
| Show products likely to stock out within the next 14 days. | `risk_view`; latest stock divided by 28-day mean demand; executed | 965.666 ms |
| What happens if demand increases by 15% at Warehouse 3? | `risk_view`; demand multiplied by 1.15 with unchanged stock; executed | 1094.167 ms |
| Why is Supplier A considered high risk? | `supplier_view`; observed lateness, counts and delay events; executed | 950.878 ms |
| Delete all delayed orders. | JSON abstention, no SQL sent to DuckDB | 0.821 ms |
| ??? write a poem about clouds | Clarification and abstention | 0.589 ms |
| Show shipments for Supplier Z | Valid SQL, zero rows, explicit empty-result abstention | 916.121 ms |
| Show shipments missing promised delivery dates | Fixture with NULL promised date; evidence shown, recommendation withheld | 932.337 ms |
| Show shipments for Supplier A, injected deadline | Worker terminated; no rows or recommendation | 8.582 ms |
| Supplier A question, injected unavailable model | No planner output; explicit unavailable status | 0.788 ms |
| Shipment question, injected malformed output | JSON rejected; no SQL execution | 0.419 ms |
| Delete request, injected destructive SQL | JSON valid; DELETE rejected by AST guard | 0.740 ms |

Each full record contains the input, retrieved views/columns/metrics/aliases/joins, unmodified planner output, JSON and SQL validation states, SQL result, measured latency, failure behavior, and clarification/abstention flags. Failure injection is explicitly labeled. Query evidence and identifiers are synthetic.

## Analytics findings

The three logistic models use separate synthetic training (720), sigmoid calibration (240) and test (240) observations. Measured test Brier scores were 0.068225 for stockout, 0.179005 for late delivery and 0.060392 for supplier reliability. These measure agreement with the simulation's labels only. The runtime shows qualitative risk levels and evidence drivers, not confidence percentages.

For synthetic Product P1 at Warehouse W3, six chronological 14-day forecasting folds gave mean absolute error 2.655792 units for quantile histogram gradient boosting, versus 3.5 units for seasonal naive. The 0.1/0.9 quantile bounds covered only 44 of 84 held-out observations (52.38%). These intervals under-cover and must not be presented as reliable 80% intervals. Sparse histories use a warned baseline; very short histories receive no interval. Demand spikes trigger a warning.

The decision test edited a pending simulation, explicitly approved that revision, and rejected a second proposal. No external action occurred. All 41 audit events verified. The test reviewer is a fixture, not a claim that a person approved these actions.

## Limits and next experiment

The offline planner supports a bounded set of question forms. Model adapters, large-schema generalization and QLoRA still need execution on a suitable host. The synthetic training templates share task forms across disjoint entity splits, so their validation loss cannot establish paraphrase generalization. Forecast intervals need calibration and broader testing. The audit mechanism is a local, single-operator application log; a database owner can rewrite its contents and hashes, and it does not authenticate reviewers or provide an externally anchored ledger.

Eleven core Kaggle datasets were downloaded and profiled for schema research. Backorder competition access returned `UnauthenticatedError`. Nine benchmark competitions remain registered but unattempted. Reported licenses and measured profile fields are in `data/dataset_manifest.csv`; none of those raw records enter the demo or training examples.

Implementation references: [Qwen model card](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct), [PEFT quantization guide](https://huggingface.co/docs/peft/developer_guides/quantization), [KaggleHub](https://github.com/Kaggle/kagglehub), and [histogram gradient boosting reference](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).
