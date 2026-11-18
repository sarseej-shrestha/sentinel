# Sentinel Phase 4 implementation handoff

SIGSEGV · Sarseej Shrestha · CMPS 4200 HCI

## Starting point

This work is on `sentinel-phase4-integration`. The latest remote participant-readiness commit was `fd98480b6775d04b864f129145a0115411e1e054`. The local `sentinel-phase3-gruvbox` checkout had the subsequent Analyze layout, connected map, inspector, and study controls as working-tree changes. Commit `4f41b73` preserves that baseline on the integration branch. Historical 749 Python and 28 browser tests were reported for participant readiness. The untracked `build_phase2_presentation.cjs` remains local. No standalone Phase 4 rubric file was found in the repository or the checked local submission locations. The supplied brief governs this handoff.

## Setup

From the repository root, using the lockfile's tested environment:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install -e . --no-deps
npm --prefix frontend ci
npm --prefix frontend run build
sh scripts/download_phase4.sh
.venv/bin/python -m scripts.train_phase4
```

The public version-1 archives were accessible without Kaggle credentials on 2026-11-18. The trainer verifies the decompressed CSV checksums. If the public endpoint later changes or requires authentication, download the same version through an authorized Kaggle account and place the archives in `data/raw/phase4/retail.zip` and `data/raw/phase4/shipment.zip`. Training uses seed 4200. It writes a versioned local model bundle, manifest with a bundle digest, and a reason-to-CSV-record exclusion index under `data/processed/phase4/`. Keep this directory writable only by the workspace operator. The bundle is trusted local joblib with datasets and models, and should be recreated from the pinned inputs. Raw archives and artifacts stay out of Git.

For the local Qwen provider, install the optional model dependencies if needed. The tested local cache used Transformers 4.57.6 and PyTorch 2.14.1. If the checkpoint is absent, download through the public Hugging Face model repository once before running offline:

```sh
.venv/bin/python -m pip install -e '.[models]'
.venv/bin/python -c 'from huggingface_hub import snapshot_download; snapshot_download("Qwen/Qwen2.5-Coder-1.5B-Instruct")'
cp -n .env.example .env
```

Terminal 1:

```sh
HF_HUB_OFFLINE=1 .venv/bin/python -m scripts.qwen_server
```

Terminal 2:

```sh
set -a
. ./.env
set +a
.venv/bin/python -m sentinel.web --init --data-dir data/processed/phase4-demo --port 8767
```

Open http://127.0.0.1:8767. `--init` creates a missing synthetic fixture only. `SENTINEL_QWEN_URL` is the full OpenAI-compatible completion endpoint. A different authorized Qwen host can be configured with `SENTINEL_QWEN_MODEL` and an optional `SENTINEL_QWEN_TOKEN`. Keep the token in the server environment and do not open `.env` on camera. `git check-ignore .env` verifies the ignore rule. If the model endpoint is unavailable, analysis, history and the map remain usable, and chat returns a retryable error.

## Feature contracts and source scopes

| Feature | HTTP endpoint | Model or calculation |
| --- | --- | --- |
| Sentinel assistant | `POST /api/chat` | Qwen2.5-Coder-1.5B-Instruct. The model selects an interpretation intent and verified fact keys. The server builds the explanation, inserts figures and checks snapshot citations. |
| Published retail forecast | `GET /api/published/forecast` | Three scikit-learn histogram gradient boosting regressors, quantile loss 0.1, 0.5 and 0.9, trained across store/product series. Runs recursive held-out-origin inference from the artifact. |
| Published coverage and what-if | `GET /api/published/coverage` | Opening stock divided by prior 28-day mean sales. What-if increases the divisor with stock fixed and replenishment held at zero. |
| Published shipment lateness | `GET /api/published/lateness` | Delivered date after scheduled date among valid SCMS line items. Vendor filters summarize observed frequency. |
| Shipment evaluation | `GET /api/published/shipment-score` | StandardScaler on quantity and one-hot encoding on country and product group, logistic regression, then sigmoid calibration on a separate chronological partition. Returns retrospective scores for catalog-listed held-out examples. |
| Existing Analyze workspace | `POST /api/analyses` | Synthetic fixture only. Quantile histogram gradient boosting for the 14-day fixture forecasts prepared at startup. Dates, coverage, what-if and review priorities use deterministic calculations and rules. |
| Connected map and replay | `GET /api/map/…` and existing history/evidence endpoints | Record relationships in the synthetic fixture, read-only source inspector, and saved evidence replay. |

Chat receives a question, up to two prior questions, and server-resolved facts for one scope. Supported scopes are a verified analysis, a published result selection, or the last verified map record opened in the inspector. The client sends scope identifiers. Imported free text, reviewer notes and raw rows stay outside the prompt. Chat has no action tools, generated URLs, or SQL execution. Invalid model choices and unreachable providers fail explicitly. Cancellation aborts the client request and upstream wait. Generation in the local provider can continue until its short generation limit when a connection closes. Qualitative explanations are constrained to the intents in `chat.py`. No embedding model runs in this path. The legacy CLI can still run its separate synthetic classifier and diagnostic planner.

The published sources are independent of each other and of the synthetic workspace, map and saved review state. The Published data view shows historical source lineage and is read-only.

**Optional file-to-map:** use Supply Chain Map → Import relationships. Download the CSV template or example, select a CSV or XLSX up to 96 KiB and 40 relationship rows, and choose a worksheet when requested. Preview shows accepted and rejected row counts, numbered errors, example rows and proposed node and wire counts. Name the map and confirm the accepted rows. Identical confirmation is idempotent. The selector and source inspector expose persisted maps and contributing CSV/workbook rows. Imports live in a separate local SQLite file beside the workspace state. The import validates relationship structure and does not train models.

## Data and evaluation

The [committed evaluation manifest](phase4-evaluation.json) contains the source URLs, filenames, exact checksums, licenses, input schemas, mappings, observation dates, import dispositions, split counts, per-horizon forecast metrics and model results. Source row identifiers are one-based CSV record ordinals including the header. The local bundle preserves accepted identifiers and its exclusion index lists rejected identifiers by reason.

- **Retail Store Inventory Forecasting Dataset, `anirudhchauhan/retail-store-inventory-forecasting-dataset`, v1.** Kaggle publisher identifies the observations as synthetic and the data as CC0. All 73,100 records cover 100 complete store/product series daily from 2022-01-01 through 2024-01-01. Recorded zeros are retained. Missing days are never filled. The forecast target is units sold. Publisher-provided Demand Forecast, inventory and future features are absent from training inputs. Inventory Level is documented as opening-day stock. Sales can understate demand. The data do not establish stockout labels or replenishment events suitable for depletion predictions.
- **Supply Chain Shipment Pricing Data, `apoorvwatsky/supply-chain-shipment-pricing-data`, v1.** Kaggle uploader attributes the health commodity SCMS source to USAID dataset `0162a542-4f2e-4fe2-ad5d-8f6ed2344056`. Kaggle lists “Data files © Original Authors.” Redistribution permission remains unresolved. All 10,324 line items were valid for descriptive lateness from 2006-05-02 to 2015-09-14. The retrospective prediction evaluation excludes 6,459 items, chiefly for missing PO dates or order identifiers. Each CSV record is a line item. PO send date is the assumed prediction time for the research reconstruction. Country, product group and quantity are modeled as order attributes. The extract has no complete version history for order edits, which limits deployment conclusions. Scheduled and delivered dates define the label, while actual dates, current schedule, final statuses, carrier and other fulfillment fields are absent from prediction inputs. Orders straddling splits are removed using order IDs before exclusions. Outcomes not delivered and recorded by the next phase boundary are purged. Supplier forecasting remains unsupported.

For the forecast, three 14-day rolling-origin validation windows select the quantile model when it beats seven-day seasonal naive on both MAE and RMSE at every origin. The final fit uses data through 2023-12-18. The untouched 2023-12-19 through 2024-01-01 window has 1,400 predictions. Boosting MAE is **83.46** and RMSE **106.62** units, with **81.9%** empirical interval coverage. Seasonal-naive MAE is **115.26**, RMSE **146.06**, and residual-range coverage **83.0%**. The weakest boosted series by final MAE is S002/P0007. The manifest records every horizon, including weak horizons. Intervals are empirical historical estimates. The demo depicts forecasts at the held-out historical origin.

Shipment train, calibration and final periods use PO dates before 2012, in 2012–2013, and in 2014 onward. There are **1,996 / 904 / 965** eligible line items and **7.16% / 6.97% / 3.83%** late rates. Calibrated final ROC AUC is **0.668**, average precision **0.056**, Brier **0.0603** and log loss **0.2168**. Uncalibrated Brier is **0.0394**, and training-prevalence Brier is **0.0380**. The calibration degraded probability accuracy under the later distribution. The classifier is retained as an explicitly marked model-evaluation demo. No action threshold or operational shipment probability was promoted.

## Verification

```sh
.venv/bin/python -m scripts.check_release
CI=1 npm --prefix frontend run test:e2e
.venv/bin/python scripts/quality_gate.py
node frontend/scripts/phase4-live.mjs
```

The integration run passed **760 Python tests**, **33 Chromium tests**, the full release runner and the staged quality gate. The separate live run rendered all four published result paths, recovered from a genuine Qwen upstream 503 without retyping, and passed native 200% Chromium zoom for the map and chat with a scoped axe scan. The model can choose an unhelpful intent on ambiguous prompts. The source inspector and forecast tables keep the verified facts directly available.

The standard browser suite uses disposable storage and injected responses for chat failures. The live script needs Qwen already listening on port 8769 and Chromium installed. It uses disposable ports 8770 and 8771, exercises rendered published analytics, and verifies recovery from a genuine upstream connection failure by restoring connectivity to real Qwen. It also checks native Chromium 200% zoom. The separate [recording script and live run sheet](phase4-demo.md) cover the supplied presentation and submission requirements.
