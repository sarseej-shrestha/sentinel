# CMPS 4200 Phase 2 design artifacts

These editable SVGs preserve the Phase 2 proposed information architecture, user flows and low-fidelity screens. The completion branch now implements the five-workflow React/FastAPI workspace; see the root README for launch and verification. The SVGs remain historical designs, not screenshots. Interviews, genuine personas and human usability findings are still not claimed.

## Artifact index

- `assets/site_map.svg`: six screen types and analysis variants.
- `assets/data_flow.svg`: authoritative local path and isolated diagnostics.
- `assets/ai_states.svg`: initial, loading, success, uncertain and error states plus recovery.
- `assets/flows/flow_1_query_clarification.svg`: request and clarification.
- `assets/flows/flow_2_risk_evidence.svg`: risk and evidence.
- `assets/flows/flow_3_forecast.svg`: history, baseline and forecast limitations.
- `assets/flows/flow_4_what_if.svg`: explicit demand scenarios.
- `assets/flows/flow_5_review_replay.svg`: human decisions and replay.
- `assets/wireframes/`: S1, four S2 result variants, S3, S4, S5 and S6; each has five state variants.

## Truthfulness and scope

All visual screens say **PROPOSED UI — not an implemented screen**. Flow roles are task placeholders, not Phase 1 personas; each is labeled **Synthetic exploratory material — not a completed user interview**. Attach genuine Phase 1 personas separately if available.

The deterministic planner is the sole authoritative answer path. Qwen and BGE are optional deferred local diagnostics; normal requests do not load them. Model proposals never compile SQL, change evidence, approve actions or alter replay. Business queries are read-only; trusted application code separately appends audit records. No external operational action occurs.

Refine/run-again controls represent new requests, not creative answer regeneration. **Revert draft** now discards unsaved frontend text before approval; it is not a backend rollback. Saved edits retain their audit history and require a separate decision. Final approved or rejected simulated decisions cannot be undone.

Timeout, unavailable service, malformed/empty model output, unsafe input and invalid evidence have explicit recovery states. Unsupported or inappropriate wording is rejected in favor of closed verified templates; this is not a completed bias/fairness evaluation. Hosted rate-limit behavior and cost are **NOT MEASURED**. No rate-limit retry feature is claimed.

## Evidence and presentation use

Source baseline: commit `80525c4d502786896de6c4c526f3f0c6a09464a2`; see the root README and `src/sentinel/` for implementation. Displayed synthetic example values come from the existing local ignored `artifacts/verified_authority_final/report.json`: September lateness counts, P1/W3 inventory coverage, and the W2 +13% demand scenario. Current shadow outcomes are four schema rejections and one formatting rejection; no successful Qwen recommendation is depicted. Diagram/plot/audit placeholders are labeled schematic rather than measured screenshots.

SVGs use a 1600 × 1100 viewBox, navy/cyan/amber styling, embedded text and vector shapes with no external assets. Insert as vectors and preserve aspect ratio; crop deliberately or split state detail into progressive reveals rather than shrinking labels. Use installed Arial/Helvetica-compatible fonts. Screen designs are not accessibility-certified; contrast, keyboard behavior and usability need review. The forecast plot is a labeled chart placeholder, not fabricated time-series values.

No backend or frozen benchmark files are changed. No PPTX is included. Speaker assignments, genuine persona mapping, human usability testing and external evaluation remain unfinished. Frontend implementation and automated browser/accessibility checks are recorded in the root README.
