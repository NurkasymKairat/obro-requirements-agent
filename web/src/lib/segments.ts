// Нарезка текста документа на отрезки по границам цитат и разметки.
// Все позиции — абсолютные смещения в DocView.text, те же, что start/end цитат в RunResult.
// Символы разметки Markdown (**, `, «- », ```) не показываются, но остаются в счёте смещений.

import type { DocBlock, Severity } from "../types";

export interface Mark {
  start: number;
  end: number;
  fid: string;
  qi: number; // номер цитаты в замечании
  side: "a" | "b" | "c"; // противоречие: цитата 1 — a, цитата 2 — b, остальные («ещё по теме») — c
  severity: Severity;
}

export interface Seg {
  start: number;
  end: number;
  text: string;
  bold: boolean;
  code: boolean;
  marks: Mark[];
}

type Range = [number, number];

interface Markup {
  hidden: Range[];
  bold: Range[];
  code: Range[];
}

const inside = (a: number, b: number, rs: Range[]) => rs.some(([s, e]) => s <= a && b <= e);

function inlineMarkdown(text: string, s: number, e: number, out: Markup) {
  const part = text.slice(s, e);
  for (const m of part.matchAll(/\*\*([^*\n]+?)\*\*/g)) {
    const a = s + m.index!;
    const b = a + m[0].length;
    out.hidden.push([a, a + 2], [b - 2, b]);
    out.bold.push([a + 2, b - 2]);
  }
  for (const m of part.matchAll(/`([^`\n]+)`/g)) {
    const a = s + m.index!;
    const b = a + m[0].length;
    out.hidden.push([a, a + 1], [b - 1, b]);
    out.code.push([a + 1, b - 1]);
  }
}

/** Разметка блока: что скрыть и что выделить. Для PDF, DOCX и TXT разметки нет. */
export function blockMarkup(text: string, block: DocBlock, s: number, e: number, md: boolean): Markup {
  const out: Markup = { hidden: [], bold: [], code: [] };
  if (!md) return out;
  if (block.kind === "heading" && block.text_start != null) {
    out.hidden.push([block.start, block.text_start]);
  } else if (block.kind === "list_item") {
    const m = /^\s*[*+-]\s+/.exec(text.slice(block.start, block.end));
    if (m) out.hidden.push([block.start, block.start + m[0].length]);
  } else if (block.kind === "code") {
    const body = text.slice(block.start, block.end);
    const first = body.indexOf("\n");
    if (body.startsWith("```")) out.hidden.push([block.start, block.start + (first < 0 ? body.length : first + 1)]);
    const last = body.lastIndexOf("\n");
    if (last > 0 && body.slice(last + 1).trim().startsWith("```")) out.hidden.push([block.start + last, block.end]);
    return out;
  }
  inlineMarkdown(text, s, e, out);
  return out;
}

/** Отрезки текста [s, e) с одинаковым набором цитат и одинаковым начертанием. */
export function segments(text: string, s: number, e: number, marks: Mark[], markup: Markup): Seg[] {
  const local = marks.filter((m) => m.start < e && m.end > s);
  const cuts = new Set<number>([s, e]);
  const add = (x: number) => {
    if (x > s && x < e) cuts.add(x);
  };
  for (const m of local) {
    add(m.start);
    add(m.end);
  }
  for (const rs of [markup.hidden, markup.bold, markup.code]) for (const [a, b] of rs) (add(a), add(b));
  const pts = [...cuts].sort((x, y) => x - y);
  const out: Seg[] = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    if (inside(a, b, markup.hidden)) continue;
    out.push({
      start: a,
      end: b,
      text: text.slice(a, b),
      bold: inside(a, b, markup.bold),
      code: inside(a, b, markup.code),
      marks: local.filter((m) => m.start <= a && b <= m.end),
    });
  }
  return out;
}

/** Цитата для карточки: без символов разметки Markdown; ячейки таблицы через «·». */
export const plainQuote = (s: string) =>
  s
    .replace(/\*\*([^*\n]+?)\*\*/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/^\s*\|\s*|\s*\|\s*$/g, "")
    .replace(/\s*(?<!\\)\|\s*/g, " · ");
