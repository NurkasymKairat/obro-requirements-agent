import { memo, useEffect, useMemo, useRef } from "react";
import type { JSX, ReactNode } from "react";
import { blockMarkup, segments, type Mark } from "../lib/segments";
import type { DocBlock, DocView, Severity } from "../types";
import { SEVERITIES } from "../labels";

interface Props {
  doc: DocView;
  marks: Mark[];
  selected: string | null;
  focus: { fid: string; qi: number; n: number } | null; // к какой цитате прокрутить (n — счётчик кликов)
  onPick: (fids: string[]) => void;
}

const sevRank = (s: Severity) => SEVERITIES.indexOf(s);

type Group = { kind: "table"; rows: DocBlock[] } | { kind: "block"; block: DocBlock };

function group(blocks: DocBlock[]): Group[] {
  const out: Group[] = [];
  for (const b of blocks) {
    const last = out[out.length - 1];
    if (b.kind === "table_row" && last?.kind === "table") last.rows.push(b);
    else if (b.kind === "table_row") out.push({ kind: "table", rows: [b] });
    else out.push({ kind: "block", block: b });
  }
  return out;
}

function DocumentViewImpl({ doc, marks, selected, focus, onPick }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const md = doc.format === "md";
  const groups = useMemo(() => group(doc.blocks), [doc]);

  useEffect(() => {
    if (!focus || !root.current) return;
    const el = root.current.querySelector(`[data-anchor="${focus.fid}:${focus.qi}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focus]);

  const seen = new Set<string>(); // первая часть каждой цитаты выбранного замечания — якорь для прокрутки

  const renderRange = (block: DocBlock, s: number, e: number): ReactNode[] => {
    const markup = blockMarkup(doc.text, block, s, e, md);
    return segments(doc.text, s, e, marks, markup).map((sg) => {
      let node: ReactNode = sg.text;
      if (sg.code) node = <code>{node}</code>;
      if (sg.bold) node = <strong>{node}</strong>;
      if (!sg.marks.length) return <span key={sg.start}>{node}</span>;
      const own = sg.marks.filter((m) => m.fid === selected);
      let cls: string;
      let anchor: string | undefined;
      if (own.length) {
        const m = own[0];
        const has = (side: Mark["side"]) => own.some((x) => x.side === side);
        cls = has("a") && has("b") ? "hl hl-ab" : `hl hl-${has("a") ? "a" : has("b") ? "b" : "c"}`;
        const key = `${m.fid}:${m.qi}`;
        if (!seen.has(key)) {
          seen.add(key);
          anchor = key;
        }
      } else {
        const top = sg.marks.reduce((x, y) => (sevRank(y.severity) < sevRank(x.severity) ? y : x));
        cls = `hl hl-faint sev-${top.severity}`;
      }
      const fids = [...new Set(
        [...sg.marks].sort((x, y) => sevRank(x.severity) - sevRank(y.severity) || x.fid.localeCompare(y.fid))
          .map((m) => m.fid),
      )];
      return (
        <mark
          key={sg.start}
          className={cls}
          data-anchor={anchor}
          title={fids.join(", ")}
          onClick={(ev) => {
            ev.stopPropagation();
            onPick(fids);
          }}
        >
          {node}
        </mark>
      );
    });
  };

  const renderBlock = (b: DocBlock) => {
    const body = renderRange(b, b.start, b.end);
    switch (b.kind) {
      case "heading": {
        const Tag = `h${Math.min(Math.max(b.level ?? 2, 1), 6)}` as keyof JSX.IntrinsicElements;
        return <Tag key={b.id} className="doc-h">{body}</Tag>;
      }
      case "list_item":
        return <div key={b.id} className="doc-li">{body}</div>;
      case "code":
        return <pre key={b.id} className="doc-code">{body}</pre>;
      default:
        return <p key={b.id} className="doc-p">{body}</p>;
    }
  };

  return (
    <div className="doc" ref={root}>
      {groups.map((g) =>
        g.kind === "block" ? (
          renderBlock(g.block)
        ) : (
          <div className="doc-table-wrap" key={g.rows[0].id}>
            <table className="doc-table">
              <tbody>
                {g.rows.map((row, i) => (
                  <tr key={row.id} className={i === 0 && g.rows.length > 1 ? "head" : undefined}>
                    {(row.cells ?? [{ start: row.start, end: row.end }]).map((c) =>
                      i === 0 && g.rows.length > 1 ? (
                        <th key={c.start}>{renderRange(row, c.start, c.end)}</th>
                      ) : (
                        <td key={c.start}>{renderRange(row, c.start, c.end)}</td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ),
      )}
    </div>
  );
}

export const DocumentView = memo(DocumentViewImpl);
