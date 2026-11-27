import { VERDICT_SHORT, gist, modelName, passName, plural, seconds, shortDate, shortVersion } from "../labels";
import type { Finding, Loaded } from "../types";

type FailedStage = { name: string; status: string; error: string | null };

const stageState = (s: FailedStage) =>
  s.status === "skipped" ? "пропущен" : s.status === "pending" ? "не начат" : "сбой";

/** Плашка неполного прогона. compact — для полосы итога: подробности раскрываются по клику. */
export function IncompleteBanner({ reason, stages, compact = false }: { reason: string | null; stages: FailedStage[]; compact?: boolean }) {
  const list = stages.length > 0 && (
    <ul>
      {stages.map((s) => (
        <li key={s.name}>
          <strong>{passName(s.name)}</strong> — {stageState(s)}
          {s.error && <span className="err">: {s.error.replace(/^пропущен:\s*/, "")}</span>}
        </li>
      ))}
    </ul>
  );
  const why = reason && (
    <p className="reason">
      <span>Причина:</span> {reason}
    </p>
  );
  if (compact)
    return (
      <div className="incomplete compact" role="alert">
        <div className="incomplete-title">Прогон неполный</div>
        <p className="one-line">
          Вердикт не выносится: не выполнено {stages.length} {plural(stages.length, "проход", "прохода", "проходов")}.
        </p>
        <details className="popover">
          <summary>Что не выполнено</summary>
          <div className="popover-body">
            {list}
            {why}
          </div>
        </details>
      </div>
    );
  return (
    <section className="incomplete" role="alert">
      <h2 className="incomplete-title">Прогон неполный</h2>
      <p>Часть проверок не выполнена, поэтому вердикт не выносится.</p>
      {list}
      {why}
    </section>
  );
}

function Verdict({ run }: { run: Loaded }) {
  const fixes = run.result.first_fixes;
  return (
    <div className={`verdict verdict-${run.result.verdict}`}>
      <div className="verdict-text" title={run.view.verdict_text}>
        {VERDICT_SHORT[run.result.verdict]}
      </div>
      {fixes.length > 0 && (
        <details className="popover">
          <summary>Что исправить первым</summary>
          <div className="popover-body">
            <ol>
              {fixes.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ol>
          </div>
        </details>
      )}
    </div>
  );
}

/** «9 вызовов», «9 ответов из кеша», «5 вызовов + 4 из кеша». */
export function callsText(calls: number, cached: number): string {
  if (cached > 0 && calls === 0) return `${cached} ${plural(cached, "ответ", "ответа", "ответов")} из кеша`;
  const c = `${calls} ${plural(calls, "вызов", "вызова", "вызовов")}`;
  return cached > 0 ? `${c} + ${cached} из кеша` : c;
}

/** Полоса итога: вердикт · счётчики · паспорт. */
export function ResultStrip({ run, onShowKey }: { run: Loaded; onShowKey: () => void }) {
  const r = run.result;
  const s = run.view.summary;
  const L = run.view.labels;
  const incomplete = r.verdict === "incomplete" || !!r.incomplete_reason;
  const calls = run.view.totals.calls;
  const cached = run.view.totals.cache_hits;
  return (
    <section className="strip">
      <div className="strip-verdict">
        {incomplete ? (
          <IncompleteBanner reason={r.incomplete_reason} stages={run.view.failed_stages} compact />
        ) : (
          <Verdict run={run} />
        )}
      </div>

      <div className="strip-counters">
        <div className="counts">
          <div className="counts-main">
            <span className="counter">
              <span className="num">{s.requirements}</span>
              <span className="cap">{plural(s.requirements, "требование", "требования", "требований")}</span>
            </span>
            <span className="counter">
              <span className="num">{s.confirmed}</span>
              <span className="cap">
                {plural(s.confirmed, "замечание", "замечания", "замечаний")}
                {s.potential > 0 && <small>+{s.potential} требуют уточнения</small>}
              </span>
            </span>
          </div>
          <div className="sev-line">
            {(["critical", "high", "medium", "low"] as const).map((k) => (
              <span key={k} className={`sev-count sev-${k}`}>
                {s.by_severity[k] ?? 0} {L.severity[k]}
              </span>
            ))}
          </div>
        </div>
        <button className="key" onClick={onShowKey} title="Показать только эти замечания">
          <span className="label">Ключевые дефекты по типам</span>
          <span className="key-line">
            противоречий <b>{s.by_kind.contradiction ?? 0}</b> · непроверяемых <b>{s.by_kind.unverifiable ?? 0}</b> ·
            неполных <b>{s.by_kind.incomplete ?? 0}</b>
          </span>
        </button>
      </div>

      <div className="strip-passport">
        <div title={`${r.provider} · ${r.model}`}>
          {modelName(r.model)} · {callsText(calls, cached)} · {seconds(run.view.totals.seconds)}
          {cached > 0 && calls === 0 ? " (из кеша)" : ""}
        </div>
        <div className="mono" title={`Версия агента: ${r.agent_version}; ${r.date}`}>
          версия {shortVersion(r.agent_version)} · {shortDate(r.date)}
        </div>
      </div>
    </section>
  );
}

export function TopDefects({ run, byId, selected, onSelect }: { run: Loaded; byId: Map<string, Finding>; selected: string | null; onSelect: (fid: string) => void }) {
  const top = run.view.main_fids.map((id) => byId.get(id)).filter((f): f is Finding => !!f);
  if (!top.length) return null;
  const L = run.view.labels;
  return (
    <section className="top" aria-label="Главное">
      <h2 className="top-label">Главное</h2>
      <ol>
        {top.map((f) => (
          <li
            key={f.fid}
            onClick={() => onSelect(f.fid)}
            className={`sev-${f.severity}${f.fid === selected ? " selected" : ""}`}
            title={f.description}
          >
            <div className="top-head">
              <span className="fid">{f.fid}</span>
              <span className={`badge sev sev-${f.severity}`}>{L.severity[f.severity]}</span>
            </div>
            <div className="top-gist">{gist(f.description)}</div>
            <div className="loc">{f.quotes[0]?.location.split(" › ").pop()}</div>
          </li>
        ))}
      </ol>
    </section>
  );
}
