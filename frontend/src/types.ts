export type Kind =
  | "supplier_delay"
  | "stockout"
  | "forecast"
  | "supplier_risk"
  | "what_if";
export type Task = {
  kind: Kind;
  supplier_id?: string;
  product_id?: string;
  warehouse_id?: string;
  start_date?: string;
  end_date?: string;
  horizon_days?: number;
  increase_percent?: number;
};
export type Row = Record<string, string | number | boolean | null>;
export type Recommendation = {
  id: string;
  label: string;
  text: string;
  target_row_ids: string[];
};
export type Analysis = {
  analysis_id: string;
  state: "success" | "empty" | "uncertain" | "error";
  task: Task | null;
  interpretation: string;
  plan: Record<string, unknown>;
  reference_date: string;
  dataset_fingerprint: string;
  evidence_id: string | null;
  row_count: number;
  message: string;
  warnings: string[];
  rows: Row[];
  recommendations: Recommendation[];
  forecast: null | {
    method: string;
    history_days: number;
    points: { date: string; forecast: number; baseline: number }[];
  };
  elapsed_ms: number;
};
export type Action = {
  id: string;
  analysis_id: string;
  recommendation_id: string;
  evidence_id: string;
  target_row_ids: string[];
  state: "pending" | "approved_simulation" | "rejected";
  revision: number;
  text: string;
  reviewer: string;
  simulated_action_only: true;
  external_action_executed: false;
};
export type Evidence = {
  id: string;
  rows: Row[];
  row_ids: string[];
  total: number;
  offset: number;
  verified: boolean;
  reference_date: string;
  dataset_fingerprint: string;
  included_count: number;
  excluded_count: number;
  truncated: boolean;
  sql: string;
  parameters: Record<string, unknown>;
  versions: Record<string, unknown>;
};
export type History = {
  verified: boolean;
  event_count: number;
  head_hash: string;
  actions: Action[];
  events: {
    event_id: string;
    event_type: string;
    timestamp: string;
    actor: string;
    payload: Record<string, unknown>;
    event_hash: string;
    previous_hash: string;
  }[];
};
export type Catalog = {
  reference_date: string;
  dataset_fingerprint: string;
  entities: Record<string, Record<string, string[]>>;
  synthetic: true;
  application_version: string;
};
