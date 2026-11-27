import { KINDS, SEVERITIES, STATUSES, STATUS_FILTER_RU } from "../labels";
import type { Finding, Kind, ResultView, Severity, Status } from "../types";

export interface FilterState {
  severity: Set<Severity>;
  kind: Set<Kind>;
  status: Set<Status>;
}

export const emptyFilters = (): FilterState => ({ severity: new Set(), kind: new Set(), status: new Set() });

/** По умолчанию — только подтверждённые: так же считает полоса итога и отчёт. */
export const defaultFilters = (): FilterState => ({ ...emptyFilters(), status: new Set<Status>(["confirmed"]) });

const isDefault = (s: FilterState) =>
  !s.severity.size && !s.kind.size && s.status.size === 1 && s.status.has("confirmed");

export const matches = (f: Finding, s: FilterState) =>
  (!s.severity.size || s.severity.has(f.severity)) &&
  (!s.kind.size || s.kind.has(f.kind)) &&
  (!s.status.size || s.status.has(f.status));

interface Props {
  findings: Finding[];
  labels: ResultView["labels"];
  value: FilterState;
  onChange: (v: FilterState) => void;
}

function toggle<T>(set: Set<T>, v: T): Set<T> {
  const n = new Set(set);
  if (n.has(v)) n.delete(v);
  else n.add(v);
  return n;
}

export function Filters({ findings, labels, value, onChange }: Props) {
  // чипы критичности и типа считают с учётом выбранного статуса; чипы статуса — по всем замечаниям
  const byStatus = findings.filter((f) => !value.status.size || value.status.has(f.status));
  const count = <K extends keyof Finding>(key: K, v: Finding[K]) => byStatus.filter((f) => f[key] === v).length;
  const countAll = (v: Status) => findings.filter((f) => f.status === v).length;
  const active = !isDefault(value);
  const kinds = KINDS.filter((k) => findings.some((f) => f.kind === k));

  return (
    <div className="filters">
      <div className="filter-row">
        <span className="filter-label">Критичность</span>
        {SEVERITIES.map((s) => (
          <button
            key={s}
            className={`chip sev-${s}${value.severity.has(s) ? " on" : ""}`}
            disabled={!count("severity", s)}
            onClick={() => onChange({ ...value, severity: toggle(value.severity, s) })}
          >
            {labels.severity[s]} <span className="n">· {count("severity", s)}</span>
          </button>
        ))}
      </div>
      <div className="filter-row">
        <span className="filter-label">Тип</span>
        {kinds.map((k) => (
          <button
            key={k}
            className={`chip${value.kind.has(k) ? " on" : ""}`}
            onClick={() => onChange({ ...value, kind: toggle(value.kind, k) })}
          >
            {labels.kind[k]} <span className="n">· {count("kind", k)}</span>
          </button>
        ))}
      </div>
      <div className="filter-row">
        <span className="filter-label">Статус</span>
        {STATUSES.map((s) => (
          <button
            key={s}
            className={`chip${value.status.has(s) ? " on" : ""}`}
            disabled={!countAll(s)}
            onClick={() => onChange({ ...value, status: toggle(value.status, s) })}
          >
            {STATUS_FILTER_RU[s]} <span className="n">· {countAll(s)}</span>
          </button>
        ))}
        {active && (
          <button className="link reset" onClick={() => onChange(defaultFilters())}>
            Сбросить
          </button>
        )}
      </div>
    </div>
  );
}
