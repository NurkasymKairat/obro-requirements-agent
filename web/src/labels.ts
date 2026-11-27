import type { Kind, Severity, Status, Verdict } from "./types";

// Подписи по умолчанию; в ответе API приходят подписи ядра (agent/report.py) и имеют приоритет.
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];
export const KINDS: Kind[] = ["contradiction", "unverifiable", "incomplete", "ambiguous", "design_in_requirement", "other"];
export const STATUSES: Status[] = ["confirmed", "potential"];

export const SEVERITY_RU: Record<Severity, string> = {
  critical: "критично",
  high: "высокая",
  medium: "средняя",
  low: "низкая",
};

export const KIND_RU: Record<Kind, string> = {
  ambiguous: "неоднозначность",
  unverifiable: "непроверяемость",
  contradiction: "противоречие",
  incomplete: "неполнота",
  design_in_requirement: "решение вместо требования",
  other: "прочее",
};

export const STATUS_FILTER_RU: Record<Status, string> = {
  confirmed: "подтверждённые",
  potential: "требуют уточнения",
};

// Вердикт на экране — одна короткая фраза; полный текст ядра (VERDICT_RU) — в подсказке.
export const VERDICT_SHORT: Record<Verdict, string> = {
  ready: "Готов к разработке",
  ready_with_fixes: "Готов после исправлений",
  not_ready: "К разработке не готов",
  incomplete: "Прогон неполный",
};

const PASS_RU: Record<string, string> = {
  extract: "Нарезка на требования",
  rules: "Правила без модели",
  individual: "Проверка каждого требования",
  cross_acceptance: "Требования против критериев приёмки",
  cross_parts: "Документ против спецификации API",
  parts_goals: "Части документа и цели",
  acceptance_table: "Таблица критериев приёмки",
  cross: "Перекрёстная проверка",
  states_units: "Состояния, единицы и границы",
  review: "Ревью и слияние замечаний",
};

export const passName = (name: string) => PASS_RU[name] ?? name;

export function seconds(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} с`;
  const m = Math.floor(s / 60);
  return `${m} мин ${Math.round(s - m * 60)} с`;
}

export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

/** «gemini-3.8-flash» -> «Gemini 3.8 Flash»; «gpt-6-sol» -> «GPT 6 Sol». Несколько моделей — через запятую. */
export function modelName(model: string): string {
  return model
    .split(",")
    .map((m) =>
      m
        .trim()
        .split("-")
        .map((w) => (/^(gpt|o\d)$/i.test(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
        .join(" "),
    )
    .join(", ");
}

/** Суть дефекта одной строкой: первое предложение описания, не длиннее max знаков. */
export function gist(text: string, max = 110): string {
  const t = text
    .replace(/\s*\([^()]*строк[аи][^()]*\)/g, "") // место и так подписано в карточке
    .replace(/\s+/g, " ")
    .trim();
  const m = /^(.+?[.!?])(?=\s+[А-ЯЁA-Z«"(]|$)/.exec(t);
  const first = m ? m[1] : t;
  if (first.length <= max) return first;
  const cut = first.slice(0, max - 1);
  return cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 20)).replace(/[,;:\s]+$/, "") + "…";
}

/** «2026-11-27 12:45 +05» -> «27.11.2026 12:45»; непонятный формат — как есть. */
export function shortDate(date: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/.exec(date);
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}` : date;
}

/** «0.1.0+d4f6c5d» или «0.1.0+d4f6c5d-dirty» -> «d4f6c5d». */
export function shortVersion(v: string): string {
  const m = /\+([0-9a-f]{7,40})/i.exec(v);
  return m ? m[1] : v;
}

/** Секунды -> «01:48». */
export function clock(s: number | null | undefined): string {
  if (s == null) return "—";
  const t = Math.round(s);
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}

export function fileSize(bytes: number | undefined): string {
  if (!bytes) return "";
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} КБ` : `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}
