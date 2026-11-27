import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { reportUrl } from "../api";
import type { Mark } from "../lib/segments";
import type { Finding, Loaded, Requirement } from "../types";
import { DocumentView } from "./DocumentView";
import { FindingCard, quoteSide } from "./FindingCard";
import { Filters, defaultFilters, matches, type FilterState } from "./Filters";
import { ResultStrip, TopDefects } from "./Overview";

interface Props {
  run: Loaded;
  title: string;
  onNew: () => void;
}

export function ResultScreen({ run, title, onNew }: Props) {
  const findings = run.result.findings;
  const byId = useMemo(() => new Map(findings.map((f) => [f.fid, f])), [findings]);
  const reqs = useMemo(() => new Map<string, Requirement>(run.result.requirements.map((r) => [r.rid, r])), [run]);
  const [filters, setFilters] = useState<FilterState>(defaultFilters);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ fid: string; qi: number; n: number } | null>(null);
  const [onlyFids, setOnlyFids] = useState<Set<string> | null>(null); // «ключевые дефекты по типам» из полосы итога
  const cards = useRef(new Map<string, HTMLElement>());
  const clicks = useRef(0);

  const visible = useMemo(
    () => findings.filter((f) => matches(f, filters) && (!onlyFids || onlyFids.has(f.fid))),
    [findings, filters, onlyFids],
  );

  const marks = useMemo<Mark[]>(() => {
    const out: Mark[] = [];
    const add = (f: Finding) =>
      f.quotes.forEach((q, qi) =>
        out.push({
          start: q.start,
          end: q.end,
          fid: f.fid,
          qi,
          side: quoteSide(f, qi),
          severity: f.severity,
        }),
      );
    visible.forEach(add);
    const sel = selected ? byId.get(selected) : undefined;
    if (sel && !visible.includes(sel)) add(sel);
    return out;
  }, [visible, selected, byId]);

  const scrollCard = (fid: string) =>
    requestAnimationFrame(() => cards.current.get(fid)?.scrollIntoView({ behavior: "smooth", block: "nearest" }));

  // клик по карточке или по цитате в карточке: выделить и прокрутить текст к цитате
  const select = useCallback((fid: string, qi = 0) => {
    setSelected(fid);
    setFocus({ fid, qi, n: ++clicks.current });
  }, []);

  // клик в блоке «Главное»: перейти к рабочей области, показать замечание
  const selectFromTop = (fid: string) => {
    if (!visible.some((f) => f.fid === fid)) {
      setFilters(defaultFilters());
      setOnlyFids(null);
    }
    select(fid);
    scrollCard(fid);
  };

  // клик по подсвеченному месту в тексте: самое критичное замечание; повторный клик — следующее
  const pick = useCallback(
    (fids: string[]) => {
      setSelected((cur) => {
        const i = cur ? fids.indexOf(cur) : -1;
        const next = fids[(i + 1) % fids.length];
        scrollCard(next);
        return next;
      });
    },
    [],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const selectedF = selected ? byId.get(selected) : undefined;

  return (
    <div className="result">
      <header className="topbar">
        <div className="brand" onClick={onNew} role="button">
          <span className="logo">✓</span> {title}
        </div>
        <div className="doc-name" title={run.result.document}>
          {run.id === "demo" && <span className="badge demo">пример</span>}
          {run.result.document.split("/").pop()}
        </div>
        <div className="actions">
          <a className="btn" href={reportUrl(run.id)} download>
            Скачать отчёт
          </a>
          <button className="btn primary" onClick={onNew}>
            Новый документ
          </button>
        </div>
      </header>

      {run.mismatched_quotes ? (
        <div className="warn">
          {run.mismatched_quotes} цитат не совпадают с текстом документа по позициям: результат примера получен по
          другому документу. Проверьте WEB_DEMO_DOC и WEB_DEMO_RESULT.
        </div>
      ) : null}
      <ResultStrip
        run={run}
        onShowKey={() => {
          setFilters(defaultFilters());
          setOnlyFids(new Set(run.view.summary.key_fids));
        }}
      />
      <TopDefects run={run} byId={byId} selected={selected} onSelect={selectFromTop} />

      <div className="workspace">
        <section className="pane pane-doc">
          <div className="pane-head">
            <h2>Документ</h2>
            <div className="legend">
              {selectedF ? (
                <>
                  <span className="hl hl-a">{selectedF.kind === "contradiction" ? "сторона 1" : "цитата"}</span>
                  {selectedF.kind === "contradiction" && <span className="hl hl-b">сторона 2</span>}
                  {selectedF.kind === "contradiction" && selectedF.quotes.length > 2 && (
                    <span className="hl hl-c">ещё по теме</span>
                  )}
                  <button className="link" onClick={() => setSelected(null)}>
                    снять выделение
                  </button>
                </>
              ) : (
                <span className="hint">Подчёркнуты места замечаний. Клик по месту откроет замечание.</span>
              )}
            </div>
          </div>
          <div className="pane-body">
            <DocumentView doc={run.document} marks={marks} selected={selected} focus={focus} onPick={pick} />
          </div>
        </section>
        <section className="pane pane-findings">
          <div className="pane-head">
            <h2>
              Замечания <span className="count">{visible.length} из {findings.length}</span>
            </h2>
            {onlyFids && (
              <span className="only">
                только ключевые дефекты{" "}
                <button className="link" onClick={() => setOnlyFids(null)}>
                  показать все
                </button>
              </span>
            )}
          </div>
          <Filters findings={findings} labels={run.view.labels} value={filters} onChange={setFilters} />
          <div className="pane-body cards">
            {visible.map((f) => (
              <FindingCard
                key={f.fid}
                ref={(el) => {
                  if (el) cards.current.set(f.fid, el);
                  else cards.current.delete(f.fid);
                }}
                f={f}
                labels={run.view.labels}
                reqs={reqs}
                selected={f.fid === selected}
                onSelect={select}
              />
            ))}
            {!visible.length && <p className="empty">Нет замечаний под выбранные фильтры.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
