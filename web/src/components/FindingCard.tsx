import { forwardRef } from "react";
import { plainQuote } from "../lib/segments";
import type { Finding, Quote, Requirement, ResultView } from "../types";

interface Props {
  f: Finding;
  labels: ResultView["labels"];
  reqs: Map<string, Requirement>;
  selected: boolean;
  onSelect: (fid: string, qi?: number) => void;
}

/** Сторона цитаты для подсветки: противоречие — a, b и c («ещё по теме»); прочие замечания — a. */
export const quoteSide = (f: Finding, qi: number): "a" | "b" | "c" =>
  f.kind !== "contradiction" ? "a" : qi === 0 ? "a" : qi === 1 ? "b" : "c";

function QuoteItem({ q, side, label, onClick }: { q: Quote; side: "a" | "b" | "c"; label?: string; onClick: () => void }) {
  return (
    <li
      className={`side-${side}`}
      onClick={(ev) => {
        ev.stopPropagation();
        onClick();
      }}
      title="Показать в тексте"
    >
      {label && <span className="side-label">{label}</span>}
      <q>{plainQuote(q.text)}</q>
      <span className="loc">{q.location}</span>
    </li>
  );
}

export const FindingCard = forwardRef<HTMLElement, Props>(function FindingCard(
  { f, labels, reqs, selected, onSelect },
  ref,
) {
  const contradiction = f.kind === "contradiction";
  // у противоречия две стороны; остальные цитаты слитого замечания — «Ещё по теме», свёрнуты
  const main = contradiction ? f.quotes.slice(0, 2) : f.quotes;
  const more = contradiction ? f.quotes.slice(2) : [];
  return (
    <article
      ref={ref}
      className={`card sev-${f.severity}${selected ? " selected" : ""}${f.status === "potential" ? " potential" : ""}`}
      onClick={() => onSelect(f.fid)}
    >
      <header className="card-head">
        <span className="fid">{f.fid}</span>
        <span className={`badge sev sev-${f.severity}`}>{labels.severity[f.severity] ?? f.severity}</span>
        <span className="badge kind">{labels.kind[f.kind] ?? f.kind}</span>
        {f.status === "potential" && <span className="badge potential">требует уточнения</span>}
        <span className="scope">{f.scope === "set" ? "набор" : "требование"}</span>
      </header>
      <div className="criterion">
        {f.criterion} <span className="std">· {f.standard}</span>
      </div>
      {f.requirement_ids.length > 0 && (
        <div className="rids">
          {f.requirement_ids.map((rid) => {
            const r = reqs.get(rid);
            return (
              <span key={rid} className="rid" title={r?.summary}>
                {rid}
                {r?.source_id ? ` (${r.source_id})` : ""}
              </span>
            );
          })}
        </div>
      )}
      <p className="desc">{f.description}</p>
      <ul className="quotes">
        {main.map((q, i) => (
          <QuoteItem key={i} q={q} side={quoteSide(f, i)} label={contradiction ? `Сторона ${i + 1}` : undefined} onClick={() => onSelect(f.fid, i)} />
        ))}
      </ul>
      {more.length > 0 && (
        <details className="more-quotes" onClick={(e) => e.stopPropagation()}>
          <summary>Ещё по теме · {more.length}</summary>
          <ul className="quotes">
            {more.map((q, j) => (
              <QuoteItem key={j} q={q} side="c" onClick={() => onSelect(f.fid, j + 2)} />
            ))}
          </ul>
        </details>
      )}
      <div className="fix">
        <span className="fix-label">Как исправить</span>
        <p>{f.fix}</p>
      </div>
    </article>
  );
});
