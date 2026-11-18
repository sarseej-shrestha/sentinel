# Phase 4 recording and presentation

SIGSEGV · Sarseej Shrestha

## Preflight

Build the frontend and model artifacts, then launch the workspace as described in the [implementation handoff](phase4-handoff.md). Use the disposable `phase4-demo` workspace. Set the browser to http://127.0.0.1:8767 and select Published data. Keep a terminal ready to run the local Qwen server. Use two prepared editor tabs for `src/sentinel/api.py` and `.gitignore` with `.env.example` visible in the file tree. Keep the private `.env` contents off-screen.

Before recording, rehearse live recovery once. With the app configured to use Qwen on port 8769, stop only the Qwen process with Ctrl+C in its terminal. Submit the forecast question and wait for the assistant's unavailable message. Start the Qwen process, wait for the terminal's listening message, and click Retry message. Start the recording with Qwen stopped so the failure comes from the actual provider connection.

## Narrated screen recording, approximately 4:20

| Time | Screen and speaking notes |
| --- | --- |
| 0:00–0:30 | Introduce Sentinel and the two historical Kaggle sources. Open the connected fixture map, fit the view and open the SH367 source inspector. Say the fixture is synthetic and separate from the Kaggle sources. |
| 0:30–1:20 | In Published data, run S001/P0001 Demand forecast. Point out historical origin 2023-12-18, 14 days, units sold, quantile range, seasonal comparison, and final-window MAE. Explain that the frontend renders numbers from the persisted sklearn models on the backend. |
| 1:20–2:05 | Run Coverage and demand what-if, then Observed shipment lateness and Shipment model evaluation. Compare coverage at the default 20% increase. Show the held-out score and identify it as research only because the calibration underperformed prevalence on Brier score. |
| 2:05–3:20 | Return to the retail forecast and run it again. Open Ask Qwen and enter “Explain the forecast estimate and what it means.” Show the real failure and preserved message. Start Qwen with `HF_HUB_OFFLINE=1 .venv/bin/python -m scripts.qwen_server`, wait for startup, and use Retry message. Read the forecast explanation and its displayed model name. |
| 3:20–4:20 | Show the published and chat routes, the server-side Qwen adapter and the `.env` ignore entry. Point to the configuration filenames with secrets closed. Suggested disclosure: “I used OpenAI Codex to assist with implementation, debugging, tests and documentation. Sentinel uses local Qwen at runtime to select explanatory facts. The numeric models and calculations are implemented separately.” Amend this statement to match the team's actual use. |

## Eight-minute live presentation, then two minutes of questions

| Time | Demo |
| --- | --- |
| 0:00–0:50 | Give the problem and show Analyze, Published data, the map and decision history. Use the sidebar and theme controls. |
| 0:50–1:50 | Trace a recorded map wire to SH367 and open its source. Briefly open the Supplier B August analysis and its evidence in the synthetic workspace. |
| 1:50–3:15 | Forecast S001/P0001 from the historical origin. Show how rolling validation, untouched test and seasonal naive drive the promotion decision. Point out uncertainty and units. |
| 3:15–4:30 | Show historical coverage, say what the demand what-if holds fixed, and run observed vendor delivery history. Contrast the denominators available across sources. |
| 4:30–5:25 | Run the held-out shipment model. Explain prediction time, chronological order grouping and why the calibrated model remains research-only. |
| 5:25–6:45 | Re-run the forecast and show Qwen explain it. If the question fails, preserve the message, restart the model terminal and click Retry message after readiness. |
| 6:45–7:30 | Show endpoint contracts, the shared frontend service, and the ignored config filename. Explain how Qwen selects fact keys and Sentinel returns source-bound figures. |
| 7:30–8:00 | State AI-tool use, distinguish numerical prediction from deterministic results, and recap the main limitations. |
| 8:00–10:00 | Questions. Keep the workspace and evaluation metrics available. |

If the checkpoint cannot start during the presentation, show the unavailable state and continue through the numerical endpoints. Use the already recorded verified recovery segment as evidence of the prior run and identify it as a recording. If the Published data page reports a missing bundle, confirm the manifest and artifact exist at the configured path and restart the workspace process.
