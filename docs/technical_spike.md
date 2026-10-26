# Application-path technical spike

## Completion release measurement

Measured on an Apple M4 Max with 36 GiB RAM, macOS 15.6 arm64 / Python 3.14.3 using `python -m scripts.evaluate_release --repeats 30`. The script launches a disposable real HTTP service, uses the application’s existing recursive histogram-gradient-boosting forecast and seasonal-naïve baseline, and stores full inputs/outputs, timings, failures, source hashes and per-series/horizon results in ignored `artifacts/v1-release/release.json`. No weights were loaded or downloaded, no hosted model was called, and no parameters or intervals were tuned.

| Structured input | First measured output | First request (ms) | Warm p95 (ms), n=30 |
| --- | --- | ---: | ---: |
| `{"kind": "supplier_delay", "start_date": "2026-08-01", "end_date": "2026-08-31"}` | Supplier A: 19 late / 31 evaluable August deliveries; rate 0.612903; excluded 0 | 370.92 | 344.81 |
| `{"kind": "stockout", "horizon_days": 14}` | P1/W3: 84 units, 15.607 units/day, 5.382 days of cover | 306.68 | 308.92 |
| `{"kind": "forecast", "product_id": "P1", "warehouse_id": "W3"}` | P1/W3: first predicted day 2026-10-01, 14.43 units versus baseline 8 | 369.43 | 362.33 |
| `{"kind": "supplier_risk", "supplier_id": "S1"}` | Supplier A: 180 evaluable shipments, observed late rate 0.600; review level high | 297.76 | 294.14 |
| `{"kind": "what_if", "warehouse_id": "W3", "increase_percent": 15.0}` | Warehouse 3 +15%: P1 demand 17.948 units/day; coverage 4.680 days versus 5.382 | 285.89 | 297.97 |

Success timings include analytics, read-only worker startup, SQLite persistence and serialization. They exclude browser rendering. Warm series follow one first request per task in the same server. Successful and rejected requests are never pooled. Initial server readiness including synthetic data creation: 1166 ms. Sampled parent RSS: 370,851,840 bytes (354 MiB), excluding transient worker peaks. Large shipment analysis: 180 rows, 129,846-byte ordinary result; paginated evidence retained separately.

Two controlled failure conditions:

| Condition | Result | Time (ms) |
| --- | --- | ---: |
| Forecast request missing required fields (invalid input; no inference) | HTTP 422 with field validation error | 0.72 |
| Shipment request with deliberately injected zero query deadline | Error state; no recommendation | 20.34 |

Process restart retained pending drafts and verified history. A subsequent simulated approval and identical retry returned the same outcome. Browser tests additionally inject service/storage failures and uncertainty to exercise recovery; injected outcomes are not claimed as genuine model output.

## Recursive forecast evaluation

All 18 existing product/warehouse series have 180 contiguous daily observations. Each forecast trains only on its available prefix and recursively predicts 14 days. Development origins are indices 84, 98, 112, 126 and 140 (1,260 predictions). The final block starts at index 166 (252 predictions). There is no calibration split because bands are omitted and no interval calibration is performed. Both methods receive identical histories and origins. The final block was not used to select or tune models during this completion task. These internally authored synthetic data were exposed in prior work, so this is not a pristine independent test.

| Split | Forecast MAE / RMSE | Baseline MAE / RMSE | Predictions |
| --- | ---: | ---: | ---: |
| development | 2.669 / 3.324 | 3.531 / 4.405 | 1260 |
| final_chronological_block | 2.602 / 3.242 | 3.206 / 3.966 | 252 |

Final-block per-series results (14 predictions each):

| Series | Forecast MAE / RMSE | Baseline MAE / RMSE |
| --- | ---: | ---: |
| P1/W1 | 1.658 / 1.951 | 2.000 / 2.390 |
| P1/W2 | 2.872 / 3.308 | 3.357 / 4.149 |
| P1/W3 | 3.636 / 4.180 | 5.071 / 5.651 |
| P2/W1 | 2.796 / 3.381 | 3.286 / 3.928 |
| P2/W2 | 3.066 / 3.624 | 3.286 / 3.546 |
| P2/W3 | 3.942 / 4.801 | 3.429 / 3.891 |
| P3/W1 | 3.328 / 3.971 | 3.857 / 4.504 |
| P3/W2 | 2.799 / 3.561 | 3.143 / 4.071 |
| P3/W3 | 1.953 / 2.212 | 2.143 / 2.752 |
| P4/W1 | 2.131 / 2.541 | 2.571 / 2.952 |
| P4/W2 | 2.139 / 2.548 | 3.429 / 3.928 |
| P4/W3 | 2.295 / 2.949 | 2.643 / 3.284 |
| P5/W1 | 2.563 / 3.411 | 2.786 / 3.635 |
| P5/W2 | 2.233 / 2.836 | 3.000 / 3.566 |
| P5/W3 | 2.093 / 2.824 | 3.429 / 4.209 |
| P6/W1 | 2.179 / 2.922 | 2.786 / 4.044 |
| P6/W2 | 3.098 / 3.388 | 4.429 / 5.451 |
| P6/W3 | 2.049 / 2.584 | 3.071 / 4.009 |

Final-block horizon effects (18 predictions each):

| Day ahead | Forecast MAE / RMSE | Baseline MAE / RMSE |
| --- | ---: | ---: |
| 1 | 2.224 / 3.045 | 3.222 / 3.697 |
| 2 | 2.843 / 3.584 | 3.056 / 4.048 |
| 3 | 2.904 / 3.449 | 1.889 / 2.494 |
| 4 | 3.333 / 4.069 | 4.056 / 4.836 |
| 5 | 3.227 / 3.679 | 4.000 / 4.910 |
| 6 | 2.387 / 2.990 | 3.000 / 3.786 |
| 7 | 2.010 / 2.684 | 3.000 / 3.432 |
| 8 | 2.567 / 3.261 | 4.111 / 4.631 |
| 9 | 2.513 / 3.018 | 2.889 / 3.383 |
| 10 | 2.526 / 3.470 | 2.722 / 3.472 |
| 11 | 2.410 / 3.037 | 2.889 / 4.163 |
| 12 | 2.564 / 3.059 | 4.000 / 4.522 |
| 13 | 2.538 / 2.927 | 3.111 / 3.697 |
| 14 | 2.377 / 2.820 | 2.944 / 3.734 |

P2/W3 loses to the baseline on final-block MAE and RMSE; day 3 loses in aggregate. P2/W2 final-block RMSE is also worse despite better MAE. Development P1/W2 MAE is worse than baseline. No model was selected differently after observing these results. Historical interval coverage was insufficient; the application exposes point estimates and baseline only.

## Semantic and provenance limits

Frozen holdout: exact plans 32/48; supported positives 15/30; accepted correct 15/15; explicit clarification 7/48; safe negatives 18/18; exact negative classification 17/18. The ordinary UI therefore uses validated guided fields. These are deterministic results, not Qwen performance. Benchmarks remain internally authored and previously exposed.

Frozen SHA-256 checksums:

- `data/sample/query_plan_heldout_v1.json`: `a5c51259c8d0a34ae667a208ad67a003fb921fbfebfbedf9dc7bd63bfd1e47dc`
- `data/sample/semantic_benchmark_v2.json`: `8185221a88cb0fa8908ef7c62ce2964f20cf0041e7c5abcf2ce4b2593fcb3c38`

## Historical Phase 2 experiments

The following record is preserved for provenance. Its environment/model-availability statements describe the historical run, not the current application. Later Qwen experiments described in the README were diagnostic-only and do not establish supported semantic performance. Historical mixed success/rejection latency is not compared directly with the successful HTTP measurements above.
### Original Phase 2 technical spike

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
