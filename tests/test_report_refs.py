"""Служебные номера блоков (B-xxx) в текстах модели заменяются местом в документе при сборке отчёта."""
from agent.report import humanize_refs, humanize_result, render
from agent.schemas import Finding, Quote, Requirement, RunResult

PLACES = {
    "B-018": "Функциональные требования, строка 26",
    "B-034": "Критерии приёмки, строка 49",
    "B-036": "Критерии приёмки, строка 51",
    "B-037": "Критерии приёмки, строка 52",
    "B-046": "Общие параметры, строка 68",
}
SOURCES = {"B-018": {"П-3"}}


def h(s: str) -> str:
    return humanize_refs(s, PLACES, SOURCES)


def test_in_block_becomes_place():
    assert h("Критерий в блоке B-036 противоречит") == "Критерий (Критерии приёмки, строка 51) противоречит"


def test_place_already_named_by_requirement_is_dropped():
    assert h("требованию П-3 (блок B-018). Далее") == "требованию П-3. Далее"


def test_wrapped_bare_and_range():
    assert h("в примере (B-046) поле") == "в примере (Общие параметры, строка 68) поле"
    assert h("таблица (блоки B-034–B-037) неполна") == "таблица (Критерии приёмки, строки 49–52) неполна"


def test_no_nested_parentheses():
    assert h("(вопреки B-036)") == "(вопреки Критерии приёмки, строка 51)"
    assert h("(в блоке B-046 для валюты)") == "(Общие параметры, строка 68: для валюты)"


def test_unknown_block_removed_and_requirements_kept():
    assert h("R-012 и статус 200 (B-999) описан") == "R-012 и статус 200 описан"
    assert h("без ссылок R-001") == "без ссылок R-001"


def _result() -> RunResult:
    q = Quote(text="Сумма не больше 500", block_id="B-018", start=0, end=19,
              location="Часть A › Функциональные требования, строка 26")
    q2 = Quote(text="Перевод выполнен", block_id="B-036", start=30, end=46,
               location="Часть A › Критерии приёмки, строка 51")
    f = Finding(fid="F-001", scope="set", requirement_ids=["R-001"], criterion="согласованность", standard="ISO",
                kind="contradiction", severity="critical", status="confirmed",
                description="Критерий в блоке B-036 противоречит П-3 (B-018).", quotes=[q, q2],
                fix="Исправить B-036.", origin_pass="cross")
    r = Requirement(rid="R-001", source_id="П-3", section="Функциональные требования", summary="лимит",
                    quote=q.model_copy(update={"location": q.location + " (П-3)"}))
    return RunResult(document="x.md", date="2026-11-27", provider="gemini", model="m", agent_version="0",
                     requirements=[r], findings=[f], verdict="not_ready", first_fixes=["F-001 — x: Исправить B-036."],
                     stages=[])


def test_result_and_report_have_no_block_ids():
    res = _result()
    hr = humanize_result(res)
    assert hr.findings[0].description == "Критерий (Критерии приёмки, строка 51) противоречит П-3."
    assert res.findings[0].description.startswith("Критерий в блоке B-036")   # исходник не меняется
    md = render(res)
    body = "\n".join(line for line in md.splitlines() if not line.startswith("|"))
    assert "B-036" not in body and "B-018" not in body
    assert "Ключевые дефекты по типам: противоречий 1 · непроверяемых 0 · неполных 0" in md
