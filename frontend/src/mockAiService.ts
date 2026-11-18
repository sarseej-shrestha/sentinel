import { api } from "./api";
import type { Analysis, Task } from "./types";

export type StudyMode = "normal" | "slow" | "uncertain" | "error";

/** One explicit simulation boundary for Phase 3 study states; no live model or key. */
export async function runStudyAnalysis(
  task: Task,
  mode: StudyMode,
  referenceDate: string,
  datasetFingerprint: string,
  signal?: AbortSignal,
): Promise<Analysis> {
  if (mode === "slow") {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, 850);
      signal?.addEventListener(
        "abort",
        () => {
          window.clearTimeout(timer);
          reject(new DOMException("Cancelled", "AbortError"));
        },
        { once: true },
      );
    });
  }
  if (mode === "error") {
    throw new Error(
      "Study simulation: the analysis service is temporarily unavailable. Your inputs are preserved. Run analysis again to retry.",
    );
  }
  if (mode === "uncertain") {
    return {
      analysis_id: "study-uncertain-" + crypto.randomUUID(),
      state: "uncertain",
      task,
      interpretation:
        "Explicit study simulation; no analytical query was executed.",
      plan: {},
      reference_date: referenceDate,
      dataset_fingerprint: datasetFingerprint,
      evidence_id: null,
      row_count: 0,
      message:
        "Study simulation: available information is insufficient for a confident answer. No recommendation was made. Run analysis again for the verified dataset result.",
      warnings: [
        "This is a simulated low-confidence state for usability testing, not a result from the saved dataset.",
      ],
      rows: [],
      recommendations: [],
      forecast: null,
      elapsed_ms: 0,
    };
  }
  return api<Analysis>("/analyses", task, signal);
}
