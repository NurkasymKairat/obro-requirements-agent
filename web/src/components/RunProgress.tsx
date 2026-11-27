import { clock, fileSize, passName } from "../labels";
import type { RunView, StageProgress } from "../types";
import { HomeFrame } from "./HomeFrame";
import { IncompleteBanner } from "./Overview";

// Строка прогресса (Figma: Статус=Ожидает / Выполняется / Готово / Ошибка)
const ICON: Record<string, string> = { pending: "○", running: "◌", ok: "✓", failed: "!", skipped: "–" };

function status(s: StageProgress): string {
  const calls = s.calls ?? 0;
  const cached = s.cache_hits ?? 0;
  switch (s.status) {
    case "pending":
      return "Ожидает";
    case "running":
      return "Выполняется";
    case "failed":
      return "Ошибка";
    case "skipped":
      return "Пропущен";
    default:
      return cached > 0 && calls === 0 ? "Готово (кеш)" : "Готово";
  }
}

export function RunProgress({ run, title, onNew }: { run: RunView; title: string; onNew: () => void }) {
  const failed = run.status === "failed";
  const notDone = run.stages
    .filter((s) => s.status !== "ok")
    .map((s) => ({ name: s.name, status: s.status, error: s.error }));
  const sub = [fileSize(run.size), `прошло ${clock(run.elapsed)}`].filter(Boolean).join(" · ");

  return (
    <HomeFrame title={title}>
      {failed && <IncompleteBanner reason={run.error} stages={notDone} />}
      <section className="run-card">
        <header className="run-head">
          <span className="run-file-icon" aria-hidden>
            ▤
          </span>
          <div className="run-file">
            <div className="run-name">{run.filename}</div>
            <div className="run-sub">{sub}</div>
          </div>
          <div className="label run-count">
            {run.progress.done} из {run.progress.total} проходов
          </div>
        </header>
        <ol className="stages">
          {run.stages.map((s) => (
            <li key={s.name} className={`st st-${s.status}`}>
              <span className="st-icon">{ICON[s.status]}</span>
              <span className="st-name">{passName(s.name)}</span>
              <span className="st-status">{status(s)}</span>
              <span className="st-time">{s.status === "pending" ? "—" : clock(s.seconds)}</span>
              {s.error && s.status !== "ok" && <span className="st-err">{s.error.replace(/^пропущен:\s*/, "")}</span>}
            </li>
          ))}
        </ol>
      </section>
      {failed && (
        <button className="btn run-again" onClick={onNew}>
          Загрузить другой документ
        </button>
      )}
    </HomeFrame>
  );
}
