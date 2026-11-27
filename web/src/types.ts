// Повторяет agent/schemas.py (RunResult) и ответ api/ (RunView).

export type Severity = "critical" | "high" | "medium" | "low";
export type Kind = "ambiguous" | "unverifiable" | "contradiction" | "incomplete" | "design_in_requirement" | "other";
export type Status = "confirmed" | "potential";
export type Verdict = "ready" | "ready_with_fixes" | "not_ready" | "incomplete";

export interface Quote {
  text: string;
  block_id: string;
  start: number;
  end: number;
  location: string;
}

export interface Requirement {
  rid: string;
  source_id: string | null;
  section: string;
  summary: string;
  quote: Quote;
}

export interface Finding {
  fid: string;
  scope: "requirement" | "set";
  requirement_ids: string[];
  criterion: string;
  standard: string;
  kind: Kind;
  severity: Severity;
  status: Status;
  description: string;
  quotes: Quote[];
  fix: string;
  origin_pass: string;
}

export interface Stage {
  name: string;
  status: string;
  seconds?: number;
  calls?: number;
  error?: string;
}

export interface RunResult {
  document: string;
  date: string;
  provider: string;
  model: string;
  agent_version: string;
  requirements: Requirement[];
  findings: Finding[];
  verdict: Verdict;
  first_fixes: string[];
  stages: Stage[];
  incomplete_reason: string | null;
  wall_seconds: number | null;
  calls_seconds: number | null;
}

export interface DocBlock {
  id: string;
  kind: "heading" | "paragraph" | "list_item" | "table_row" | "code" | "other";
  start: number;
  end: number;
  location: string;
  level?: number;
  text_start?: number;
  cells?: { start: number; end: number }[];
}

export interface DocView {
  format: "md" | "txt" | "pdf" | "docx";
  text: string;
  blocks: DocBlock[];
}

export interface ResultView {
  main_fids: string[];
  summary: {
    requirements: number;
    findings: number;
    confirmed: number;
    potential: number;
    by_severity: Partial<Record<Severity, number>>;
    by_kind: Partial<Record<Kind, number>>;
    key_fids: string[]; // ключевые дефекты по типам: противоречия, непроверяемые, неполные
  };
  totals: { calls: number; cache_hits: number; seconds: number; tokens: number };
  verdict_text: string;
  failed_stages: { name: string; status: string; error: string | null }[];
  labels: { severity: Record<Severity, string>; kind: Record<Kind, string>; status: Record<Status, string> };
}

export interface StageProgress {
  name: string;
  status: "pending" | "running" | "ok" | "failed" | "skipped";
  seconds: number | null;
  calls: number | null;
  cache_hits?: number | null;
  error: string | null;
}

export interface RunView {
  id: string;
  filename: string;
  size?: number;
  status: "queued" | "running" | "done" | "failed";
  error: string | null;
  current: string | null;
  stages: StageProgress[];
  progress: { done: number; total: number };
  elapsed: number;
  mismatched_quotes?: number;
  result?: RunResult;
  view?: ResultView;
  document?: DocView;
}

export type Loaded = RunView & { result: RunResult; view: ResultView; document: DocView };
