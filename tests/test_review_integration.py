"""Подключение прохода review: решения применяются в конвейере, отчёт показывает слияния и «Требует уточнения»."""
from types import SimpleNamespace

from agent.passes import review
from agent.pipeline import Pipeline, default_passes
from agent.report import render
from agent.schemas import RawFinding, RawQuote
from tests.test_core import FIX, FakeLLM


def raw(desc, quotes, kind="ambiguous", severity="medium", rids=("R-001",)):
    return RawFinding(scope="requirement", requirement_ids=list(rids), criterion="однозначность",
                      standard="ISO/IEC/IEEE 29148 п. 5.2.5", kind=kind, severity=severity, description=desc,
                      fix="Уточнить.", quotes=[RawQuote(text=q, block_id="") for q in quotes])


class Finder:
    NAME = "finder"

    @staticmethod
    def run(client, document_text, requirements):
        return [raw("Срок без точки отсчёта", ["Бонусы сгорают через 12 месяцев."], "unverifiable", "high", ["R-002"]),
                raw("То же: срок не определён", ["через 12 месяцев"], "unverifiable", "medium", ["R-002"]),
                raw("Ставка неоднозначна", ["1 % от суммы покупки"], "ambiguous", "high"),
                raw("Спорное замечание", ["Провести покупку"], "other", "medium", [])]


def fake_review(decisions, new=()):
    """Модуль-заглушка с настоящим apply_review сессии B и заранее заданным ответом модели."""
    def run(client, document_text, requirements, findings=()):
        return review.ReviewResult(findings=list(new), decisions=decisions,
                                   potential_fids=[d.fid for d in decisions.disputed])
    return SimpleNamespace(NAME="review", run=run, apply_review=review.apply_review)


def run_with(decisions, new=()):
    return Pipeline(FakeLLM(), passes=[fake_review(decisions, new), Finder], rules=False).run(str(FIX))


def test_review_decisions_reach_report_json_and_md():
    dec = review.ReviewOutput(
        merges=[review.Merge(into="F-001", duplicates=["F-002"], reason="одна первопричина — нет точки отсчёта")],
        severities=[review.SeverityChange(fid="F-003", severity="medium", reason_code="defined_elsewhere", reason="последствие ограничено")],
        disputed=[review.Disputed(fid="F-004", reason="значение задано в критериях приёмки")])
    new = [raw("Новое противоречие с одной стороной", ["Начислено 10 бонусов"], "contradiction", "high", []),
           raw("Новый пропуск", ["Покупка 1 000 ₸"], "incomplete", "medium", [])]
    res = run_with(dec, new)

    assert [s["name"] for s in res.stages][-1] == "review"           # последним, хотя передан первым
    st = res.stages[-1]
    assert (st["merged"], st["lowered"], st["added"]) == (1, 1, 2)
    assert st["disputed"] == 2                                         # оспорено моделью + одностороннее новое
    assert "review_log" in st and "floors" in st["review_log"]

    by = {f.fid: f for f in res.findings}
    assert "F-002" not in by and len(by["F-001"].quotes) == 2          # слитое несёт все цитаты
    assert by["F-003"].severity == "medium" and by["F-004"].status == "potential"
    assert len(by) == len(set(by))                                     # номера не повторяются
    assert {"F-005", "F-006"} <= set(by)                               # новые — после наибольшего номера
    one_sided = next(f for f in res.findings if f.description.startswith("Новое противоречие"))
    assert one_sided.status == "potential"

    md = render(res)
    main = md[:md.index("## Требует уточнения")]
    assert "Объединено: F-002" in main
    assert "Бонусы сгорают через 12 месяцев." in main and "«через 12 месяцев.»" in main
    pending = md[md.index("## Требует уточнения"):md.index("## 5. Сводка")]
    assert "F-004" in pending and "значение задано в критериях приёмки" in pending
    assert "F-004" not in md[md.index("## 3."):md.index("## Требует уточнения")]
    summary = md[md.index("## 5. Сводка"):md.index("## 6. Итог")]
    confirmed = sum(1 for f in res.findings if f.status == "confirmed")
    assert f"| Нарушений | {confirmed} |" in summary and "F-004" not in summary
    assert "требуют уточнения и в сводку не входят" in summary


def test_review_in_default_passes_last():
    assert [p.NAME for p in default_passes()][-1] == "review"
