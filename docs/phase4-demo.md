# Phase 4 recording and presentation

SIGSEGV · Sarseej Shrestha

## Preflight

Build the frontend and model artifacts, then launch the workspace as described in the [implementation handoff](phase4-handoff.md). Use the disposable `phase4-demo` workspace. Set the browser to http://127.0.0.1:8767 and select Published data. Keep Ollama running with the Qwen3 checkpoint downloaded. Use two prepared editor tabs for `src/sentinel/api.py` and `.gitignore` with `.env.example` visible in the file tree. Keep the private `.env` contents off-screen.

Before recording, rehearse recovery in a dedicated demo session. To avoid stopping other Ollama work, launch an isolated listener with `OLLAMA_HOST=127.0.0.1:11435 ollama serve` and point Sentinel’s `SENTINEL_QWEN_URL` at that port. Stop only the dedicated listener with Ctrl+C. Submit a question and wait for the unavailable message. Restart the listener and use **Retry message**. Begin the failure segment with the dedicated listener stopped.

## Narrated screen recording, approximately 4:20

| Time | Screen and speaking notes |
| --- | --- |
| 0:00–0:30 | Introduce Sentinel and the two historical Kaggle sources. Open the connected fixture map, fit the view and open the SH367 source inspector. Say the fixture is synthetic and separate from the Kaggle sources. |
| 0:30–1:20 | In Published data, run S001/P0001 Demand forecast. Point out historical origin 2023-12-18, 14 days, units sold, quantile range, seasonal comparison, and final-window MAE. Explain that the frontend renders numbers from the persisted sklearn models on the backend. |
| 1:20–2:05 | Run Coverage and demand what-if, then Observed shipment lateness and Shipment model evaluation. Compare coverage at the default 20% increase. Show the held-out score and identify it as research only because the calibration underperformed prevalence on Brier score. |
| 2:05–3:20 | Return to the retail forecast and run it again. Open Ask Qwen and enter “Explain the forecast estimate and what it means.” Show the real failure and preserved message. Restart the dedicated Ollama listener and use Retry message. Explain the forecast and ask “What does that interval mean?” Show the Qwen explanation alongside Sentinel’s verified figures. |
| 3:20–4:20 | Show the published and chat routes, the server-side Qwen adapter and the `.env` ignore entry. Point to the configuration filenames with secrets closed. Suggested disclosure: “I used OpenAI Codex to assist with implementation, debugging, tests and documentation. Sentinel uses local Qwen for conversation and validates application evidence on the server. The numeric models and calculations are implemented separately.” Amend this statement to match the team's actual use. |

## Eight-minute live presentation, then two minutes of questions

| Time | Demo |
| --- | --- |
| 0:00–0:50 | Give the problem and show Analyze, Published data, the map and decision history. Use the sidebar and theme controls. |
| 0:50–1:50 | Trace a recorded map wire to SH367 and open its source. Briefly open the Supplier B August analysis and its evidence in the synthetic workspace. |
| 1:50–3:15 | Forecast S001/P0001 from the historical origin. Show how rolling validation, untouched test and seasonal naive drive the promotion decision. Point out uncertainty and units. |
| 3:15–4:30 | Show historical coverage, say what the demand what-if holds fixed, and run observed vendor delivery history. Contrast the denominators available across sources. |
| 4:30–5:25 | Run the held-out shipment model. Explain prediction time, chronological order grouping and why the calibrated model remains research-only. |
| 5:25–6:45 | Re-run the forecast and show Qwen explain it. If the question fails, preserve the message, restart the model terminal and click Retry message after readiness. |
| 6:45–7:30 | Show endpoint contracts, the shared frontend service, and the ignored config filename. Explain how Qwen generates explanations while Sentinel supplies and links verified facts. |
| 7:30–8:00 | State AI-tool use, distinguish numerical prediction from deterministic results, and recap the main limitations. |
| 8:00–10:00 | Questions. Keep the workspace and evaluation metrics available. |

If the checkpoint cannot start during the presentation, show the unavailable state and continue through the numerical endpoints. If you recorded a successful recovery during rehearsal, show that segment and identify it as a recording. If the Published data page reports a missing bundle, confirm the manifest and artifact exist at the configured path and restart the workspace process.
