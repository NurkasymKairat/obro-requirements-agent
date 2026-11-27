"""Проходы сессии B без модели: сборка промптов, таблица покрытия, оспаривание."""
import pytest

from agent.passes import ALL, DEFAULT
from agent.passes.base import block_texts, load_prompt, render_requirements
from agent.passes.calibrate import apply_floors, floor_severity, is_protected, pick_main
from agent.passes.coverage import CoverageRow, coverage_finding
from agent.passes.goals import GoalRow, goal_findings, has_goals_section, is_goal_section
from agent.passes.measurable import Aspect, MeasurableRow, is_measurable, measurable_findings, select
from agent.passes.individual import batches
from agent.passes.review import (REASONS, AddedQuote, Disputed, Merge, ReviewOutput, SeverityChange, apply_review,
                                 run as review_run)
from agent.schemas import Finding, Quote, RawFinding, RawQuote, Requirement


def req(n, source=None):
    q = Quote(text=f"Текст требования {n}", block_id=f"B-{n:03d}", start=n * 100, end=n * 100 + 10, location="")
    return Requirement(rid=f"R-{n:03d}", source_id=source, section="Раздел", summary=f"Суть {n}", quote=q)


@pytest.mark.parametrize("name", sorted(ALL))
def test_every_prompt_renders(name):
    system, user = load_prompt(name, document="[B-001] Текст", requirements="- R-001", batch="- R-001",
                               findings="- F-001", measurable="- R-001")
    assert "{{" not in system + user and "Правила для замечаний" in system and "[B-001] Текст" in user


def test_default_passes_exist():
    assert all(n in ALL for n in DEFAULT)


def test_block_text_replaces_truncated_quote():
    doc = "[B-001] # Заголовок\n[B-002] Полный текст «в кавычках».\nвторая строка\n[B-003] Хвост"
    assert block_texts(doc)["B-002"] == "Полный текст «в кавычках».\nвторая строка"
    r = req(2)
    assert "Полный текст «в кавычках»." in render_requirements([r], doc)
    assert "Суть 2" in render_requirements([r])


def test_batches_are_even_and_bounded():
    assert [len(b) for b in batches(list(range(53)), 16)] == [14, 14, 14, 11]
    assert [len(b) for b in batches(list(range(10)), 16)] == [10]
    assert batches([], 16) == []


def test_coverage_finding_is_built_by_code():
    reqs = [req(1, "Т-1"), req(2), req(3, "Т-3"), req(4)]
    rows = [CoverageRow(requirement_id="R-001", status="covered", criterion_block="B-009"),
            CoverageRow(requirement_id="R-002", status="not_covered", missing="Ввести 0 и увидеть отказ"),
            CoverageRow(requirement_id="R-003", status="partial", missing="Проверить верхнюю границу."),
            CoverageRow(requirement_id="R-004", status="not_applicable"),
            CoverageRow(requirement_id="R-999", status="not_covered")]
    f = coverage_finding(rows, reqs)
    assert f.kind == "incomplete" and f.scope == "set" and f.requirement_ids == ["R-002", "R-003"]
    assert "из 3 проверяемых" in f.description and "R-003 (Т-3)" in f.description
    assert [q.block_id for q in f.quotes] == ["B-003", "B-002"]      # сначала требования с номером
    assert "Ввести 0 и увидеть отказ." in f.fix and "Проверить верхнюю границу." in f.fix


def test_coverage_all_covered_and_incomplete_table():
    reqs = [req(1), req(2), req(3)]
    assert coverage_finding([CoverageRow(requirement_id=r.rid, status="covered") for r in reqs], reqs) is None
    with pytest.raises(ValueError):
        coverage_finding([CoverageRow(requirement_id="R-001", status="covered")], reqs)


def _finding(fid, kind="incomplete", severity="high", spans=((0, 6),), origin="individual", status="confirmed"):
    return Finding(fid=fid, scope="set", requirement_ids=[f"R-{fid[-1]}"], criterion="к", standard="с", kind=kind,
                   severity=severity, status=status, description=f"Описание {fid}", fix="-", origin_pass=origin,
                   quotes=[Quote(text="цитата", block_id="B-001", start=a, end=b, location="") for a, b in spans])


def test_review_run_returns_decisions_and_known_disputed():
    class Fake:
        def complete(self, system, user, schema, stage):
            assert "F-001" in user and "«цитата»" in user
            return ReviewOutput(disputed=[Disputed(fid="F-001", reason="есть в разделе 2"),
                                          Disputed(fid="F-404", reason="нет такого")])
    res = review_run(Fake(), "[B-001] цитата", [req(1)], findings=[_finding("F-001")])
    assert res.potential_fids == ["F-001"] and res.findings == [] and len(res.decisions.disputed) == 2


def test_apply_review_merges_recalibrates_and_demotes_one_sided():
    fs = [_finding("F-001", "contradiction", "critical", spans=((0, 6),)),
          _finding("F-002", "contradiction", "high", spans=((10, 16),), origin="cross_parts"),
          _finding("F-003", "incomplete", "critical", spans=((20, 26),), origin="rules.template"),
          _finding("F-004", "contradiction", "high", spans=((30, 36),)),
          _finding("F-005", "ambiguous", "medium", spans=((40, 46),))]
    out = ReviewOutput(
        merges=[Merge(into="F-001", duplicates=["F-002", "F-001", "F-777"], reason="одна причина")],
        severities=[SeverityChange(fid="F-003", severity="low", reason_code="formatting", reason="служебное поле"),
                    SeverityChange(fid="F-002", severity="low", reason_code="duplicate", reason="уже слито")],
        added_quotes=[AddedQuote(fid="F-004", quote=RawQuote(text="нет в документе", block_id="B-009"))],
        disputed=[Disputed(fid="F-005", reason="задано в таблице")])
    kept, log = apply_review(fs, out, lambda rq: None)
    by = {f.fid: f for f in kept}
    assert sorted(by) == ["F-001", "F-003", "F-004", "F-005"] and log["before"] == 5 and log["after"] == 4
    assert [(q.start, q.end) for q in by["F-001"].quotes] == [(0, 6), (10, 16)]      # цитаты сложены
    assert by["F-001"].origin_pass == "individual+cross_parts" and by["F-001"].requirement_ids == ["R-1", "R-2"]
    assert by["F-001"].status == "confirmed"                                          # обе стороны есть
    assert by["F-003"].severity == "high" and log["severity"][0]["lowered"]        # critical -> low: одна ступень
    assert log["severity"][0]["asked"] == "low" and log["severity"][0]["reason_code"] == "formatting"
    assert by["F-001"].severity == "critical" and log["floors"] == []
    assert by["F-004"].status == "potential" and log["one_sided"] == ["F-004"]        # вторая сторона не найдена
    assert log["quotes_rejected"][0]["fid"] == "F-004"
    assert by["F-005"].status == "potential" and len(log["ignored"]) == 3
    assert fs[0].quotes[0].start == 0 and len(fs[0].quotes) == 1                      # вход не изменён


def test_apply_review_adds_second_side():
    f = _finding("F-001", "contradiction", "high")
    q2 = Quote(text="вторая", block_id="B-002", start=50, end=56, location="")
    kept, log = apply_review([f], ReviewOutput(added_quotes=[AddedQuote(
        fid="F-001", quote=RawQuote(text="вторая", block_id="B-002"))]), lambda rq: q2)
    assert kept[0].status == "confirmed" and len(kept[0].quotes) == 2 and log["quotes_added"] == ["F-001"]


# ---------- правила кода: нижние границы степени и блок «Главное» ----------

def _q(start, location="", text="цитата"):
    return Quote(text=text, block_id="B-001", start=start, end=start + 6, location=location)


def _f(fid, kind, severity, quotes, status="confirmed", origin="individual"):
    return Finding(fid=fid, scope="set", requirement_ids=[], criterion="к", standard="с", kind=kind,
                   severity=severity, status=status, description=fid, fix="-", origin_pass=origin, quotes=quotes)


def _r(n, source, section, start):
    return Requirement(rid=f"R-{n:03d}", source_id=source, section=section, summary="с",
                       quote=Quote(text="т", block_id="B-001", start=start, end=start + 6, location=""))


REQS = [_r(1, "Т-1", "Функциональные требования", 0), _r(2, "Т-2", "Функциональные требования", 10),
        _r(3, None, "Нефункциональные требования", 20), _r(4, None, "Критерии приемки", 30),
        _r(5, "A-1", "Параметры запроса", 40)]


def test_floor_two_sided_contradiction_is_at_least_high():
    f = _f("F-001", "contradiction", "low", [_q(40), _q(60, "Часть B › Ответ")])
    assert floor_severity(f, REQS) == ("high", "противоречие с цитатами обеих сторон")
    assert apply_floors([f], REQS)[0]["to"] == "high" and f.severity == "high"


def test_floor_requirement_vs_acceptance_is_critical():
    f = _f("F-001", "contradiction", "medium", [_q(0), _q(30)])                       # раздел требования R-004
    g = _f("F-002", "contradiction", "high", [_q(0), _q(70, "Часть A › Критерии приёмки, строка 9")])
    apply_floors([f, g], REQS)
    assert f.severity == "critical" and g.severity == "critical"
    both = _f("F-003", "contradiction", "medium", [_q(30), _q(70, "Критерии приемки")])  # обе стороны в критериях
    assert floor_severity(both, REQS)[0] == "high"


def test_floor_requirements_of_one_scenario_is_critical():
    same = _f("F-001", "contradiction", "high", [_q(0), _q(10)])                     # Т-1 и Т-2, один раздел
    other = _f("F-002", "contradiction", "medium", [_q(0), _q(40)])                  # Т-1 и A-1, разные разделы
    assert floor_severity(same, REQS)[0] == "critical" and floor_severity(other, REQS)[0] == "high"


def test_floor_unverifiable_nonfunctional_is_at_least_high():
    nfr = _f("F-001", "unverifiable", "low", [_q(20)])
    fr = _f("F-002", "unverifiable", "low", [_q(0)])
    loc = _f("F-003", "unverifiable", "medium", [_q(90, "Часть A › Нефункциональные требования")])
    apply_floors([nfr, fr, loc], REQS)
    assert (nfr.severity, fr.severity, loc.severity) == ("high", "low", "high")


def test_floors_never_lower_and_skip_unproven():
    crit = _f("F-001", "contradiction", "critical", [_q(40), _q(60)])
    one_side = _f("F-002", "contradiction", "low", [_q(40)])
    potential = _f("F-003", "contradiction", "low", [_q(40), _q(60)], status="potential")
    gap = _f("F-004", "incomplete", "low", [_q(0), _q(30)])
    assert apply_floors([crit, one_side, potential, gap], REQS) == []
    assert [x.severity for x in (crit, one_side, potential, gap)] == ["critical", "low", "low", "low"]


def test_review_cannot_push_below_floor_or_jump_two_steps():
    fs = [_f("F-001", "contradiction", "critical", [_q(0), _q(30)]),      # требование против критерия приёмки
          _f("F-002", "contradiction", "high", [_q(40), _q(60)]),          # обе стороны: пол high
          _f("F-003", "unverifiable", "high", [_q(20)]),                   # НФТ: пол high
          _f("F-004", "incomplete", "critical", [_q(80)])]                 # пропуск: пола нет
    out = ReviewOutput(severities=[
        SeverityChange(fid=x.fid, severity="low", reason_code="formatting", reason="мелочь") for x in fs])
    kept, log = apply_review(fs, out, lambda rq: None, REQS)
    assert [x.severity for x in kept] == ["critical", "high", "high", "high"]
    assert [(c["from"], c["to"]) for c in log["severity"]] == [("critical", "high"), ("high", "medium"),
                                                                 ("high", "medium"), ("critical", "high")]
    assert sorted(x["fid"] for x in log["floors"]) == ["F-001", "F-002", "F-003"]


def test_severity_reason_list_is_closed():
    assert set(REASONS) == {"defined_elsewhere", "duplicate", "formatting"}
    with pytest.raises(ValueError):
        SeverityChange(fid="F-001", severity="low", reason_code="workaround", reason="есть обходной путь")


def test_pick_main_order_and_no_padding():
    fs = [_f("F-001", "incomplete", "high", [_q(0)]),
          _f("F-002", "contradiction", "high", [_q(10), _q(20)]),
          _f("F-003", "incomplete", "critical", [_q(30)]),
          _f("F-004", "contradiction", "critical", [_q(40), _q(50)]),
          _f("F-005", "unverifiable", "high", [_q(60)]),
          _f("F-006", "contradiction", "medium", [_q(70), _q(80)]),                      # medium не добивает блок
          _f("F-007", "contradiction", "critical", [_q(90), _q(100)], status="potential"),
          _f("F-008", "incomplete", "critical", [_q(110)], origin="rules.template+individual"),   # шаблонная пустота
          _f("F-009", "contradiction", "high", [_q(42), _q(120)])]                       # то же место, что F-004
    assert [f.fid for f in pick_main(fs)] == ["F-004", "F-003", "F-002", "F-005", "F-001"]
    assert [f.fid for f in pick_main(fs[5:])] == ["F-009"]
    assert pick_main([fs[5]]) == []
    many = [_f(f"F-{i:03d}", "contradiction", "critical", [_q(i * 10), _q(i * 10 + 5000)]) for i in range(8)]
    assert len(pick_main(many)) == 5


# ---------- таблица целей ----------

DOC_WITH_GOALS = "[B-001] # Сервис\n\n[B-002] ### Цели\n[B-003] Отчёт доступен сразу.\n\n[B-004] ### Требования\n[B-005] Отчёт строится ночью."
DOC_NO_GOALS = "[B-001] # Сервис\n\n[B-002] ### Требования\n[B-003] Отчёт строится ночью. Цель достигается."


def _goal(verdict, evidence=True, ids=("R-001", "R-404")):
    return GoalRow(goal=RawQuote(text="Отчёт доступен сразу.", block_id="B-003"), implemented_by=list(ids),
                   verdict=verdict, gap="отчёт появляется только на следующий день",
                   evidence=RawQuote(text="Отчёт строится ночью.", block_id="B-005") if evidence else None)


def test_goals_section_detection():
    assert has_goals_section(DOC_WITH_GOALS) and not has_goals_section(DOC_NO_GOALS)
    assert has_goals_section("[B-007] ## Назначение системы\n[B-008] текст")


def test_goal_rows_become_findings_by_code():
    out = goal_findings([_goal("yes"), _goal("partial"), _goal("no", evidence=False, ids=())], DOC_WITH_GOALS, {"R-001"})
    assert [f.kind for f in out] == ["contradiction", "incomplete"]
    assert [q.block_id for q in out[0].quotes] == ["B-003", "B-005"] and out[0].requirement_ids == ["R-001"]
    assert "только частично" in out[0].description and "следующий день" in out[0].description
    assert len(out[1].quotes) == 1 and "не прослеживается" in out[1].description and out[1].requirement_ids == []
    assert all(f.severity == "high" and f.scope == "set" for f in out)


def test_empty_goal_table_is_an_error_only_when_goals_exist():
    with pytest.raises(ValueError):
        goal_findings([], DOC_WITH_GOALS, set())
    assert goal_findings([], DOC_NO_GOALS, set()) == []
    assert goal_findings([_goal("yes")], DOC_WITH_GOALS, set()) == []


def test_parts_goals_pass_adds_table_findings():
    from agent.passes import parts_goals
    from agent.passes.goals import GoalsOutput
    own = RawFinding(scope="set", requirement_ids=[], criterion="к", standard="с", kind="incomplete", severity="low",
                     description="своё", quotes=[RawQuote(text="т", block_id="B-005")], fix="-")

    class Fake:
        def complete(self, system, user, schema, stage):
            assert schema is GoalsOutput and "таблица `goals`" in system
            return GoalsOutput(findings=[own], goals=[_goal("no")])
    res = parts_goals.run(Fake(), DOC_WITH_GOALS, [req(1)])
    assert [f.description[:5] for f in res] == ["своё", "Требо"] and res[1].kind == "contradiction"


# ---------- количественные требования ----------

def _mreq(n, text, section="Нефункциональные требования", source=None):
    return Requirement(rid=f"R-{n:03d}", source_id=source, section=section, summary="с",
                       quote=Quote(text=text, block_id=f"B-{n:03d}", start=n * 100, end=n * 100 + len(text), location=""))


def test_measurable_selection_is_done_by_code():
    yes = ["Ответ приходит за 2 секунды.", "Доступность 99,9 % в месяц.", "Лимит от 100 ₸ до 5 000 ₸.",
           "Отчёт формируется в течение рабочего дня.", "Данные обновляются немедленно.", "Хранить 3 года."]
    no = ["Клиент выбирает счёт списания.", "Код ответа 200.", "Версия 2.", "Поле содержит 20 символов.",
          "По каждому заказу хранится номер."]
    assert all(is_measurable(_mreq(i, t)) for i, t in enumerate(yes)), [t for t in yes if not is_measurable(_mreq(1, t))]
    assert not any(is_measurable(_mreq(i, t)) for i, t in enumerate(no)), [t for t in no if is_measurable(_mreq(1, t))]
    assert select([_mreq(1, yes[0]), _mreq(2, yes[0], section="Часть A › Критерии приёмки")])[0].rid == "R-001"
    assert len(select([_mreq(1, yes[0]), _mreq(2, yes[0], section="Часть A › Критерии приёмки")])) == 1


def _row(rid, **status):
    asp = {k: Aspect(status=status.get(k, "defined"), note=f"нет {k}" if status.get(k) == "not_defined" else "ок")
           for k in ("metric", "interval", "conditions", "method")}
    return MeasurableRow(requirement_id=rid, fix=status.get("fix"), **asp)


def test_unfillable_fields_become_unverifiable_findings():
    cands = [_mreq(1, "Ответ приходит за 2 секунды.", source="Н-1"), _mreq(2, "Лимит от 100 ₸ до 5 000 ₸."),
             _mreq(3, "Отчёт за 5 минут.")]
    rows = [_row("R-001", interval="not_defined", conditions="not_defined", fix="Ответ за 2 с от получения запроса."),
            _row("R-002", interval="not_applicable", conditions="not_applicable"),
            _row("R-003", method="not_defined"), _row("R-777", metric="not_defined")]
    out = measurable_findings(rows, cands)
    assert [f.requirement_ids for f in out] == [["R-001"], ["R-003"]]
    assert all(f.kind == "unverifiable" and f.scope == "requirement" for f in out)
    assert out[0].severity == "high" and out[1].severity == "medium"
    assert "R-001 (Н-1)" in out[0].description and "от какого события до какого" in out[0].description
    assert out[0].quotes[0].text == "Ответ приходит за 2 секунды." and out[0].fix.startswith("Ответ за 2 с")
    assert "как это проверяется" in out[1].fix


def test_measurable_table_must_cover_candidates():
    cands = [_mreq(i, "За 2 секунды.") for i in range(1, 5)]
    with pytest.raises(ValueError):
        measurable_findings([_row("R-001")], cands)
    assert measurable_findings([], []) == []


def test_cross_acceptance_sends_candidates_and_adds_findings():
    from agent.passes import cross_acceptance
    reqs = [_mreq(1, "Ответ приходит за 2 секунды."), _mreq(2, "Клиент выбирает счёт.", section="Требования")]

    class Fake:
        def complete(self, system, user, schema, stage):
            part = user.split("# Количественные требования для шага 4")[1]
            assert "R-001" in part and "R-002" not in part and "таблица `measurable`" in system
            return schema(findings=[], measurable=[_row("R-001", conditions="not_defined")])
    res = cross_acceptance.run(Fake(), "[B-001] Ответ приходит за 2 секунды.\n[B-002] Клиент выбирает счёт.", reqs)
    assert len(res) == 1 and res[0].kind == "unverifiable"

    class Empty:
        def complete(self, system, user, schema, stage):
            assert "таких требований нет" in user
            return schema(findings=[], measurable=[])
    assert cross_acceptance.run(Empty(), "[B-002] Клиент выбирает счёт.", reqs[1:]) == []


# ---------- слияние с дописыванием сути и исключение целей из количественных ----------

def test_merge_appends_gist_only_for_another_place():
    main = _f("F-001", "incomplete", "medium", [_q(0)])
    main.description = "Чек-лист не заполнен."
    same = _f("F-002", "incomplete", "medium", [_q(2)]); same.description = "Тот же чек-лист пуст."
    other = _f("F-003", "incomplete", "medium", [_q(500)]); other.description = "Версия и дата документа не указаны. " + "Подробности. " * 40
    out = ReviewOutput(merges=[Merge(into="F-001", duplicates=["F-002", "F-003"], reason="одна причина")])
    kept, log = apply_review([main, same, other], out, lambda rq: None)
    d = kept[0].description
    assert d.startswith("Чек-лист не заполнен. Сюда же относится (F-003): Версия и дата документа не указаны.")
    assert "F-002" not in d and "Тот же чек-лист" not in d and len(d) < 400
    assert log["appended"] == [{"into": "F-001", "fid": "F-003"}] and len(kept[0].quotes) == 3
    assert main.description == "Чек-лист не заполнен."                 # вход не изменён


def test_goal_statements_are_not_measurable_candidates():
    assert is_goal_section("Часть A › Цели") and is_goal_section("Назначение системы")
    assert not is_goal_section("Часть A › Нефункциональные требования") and not is_goal_section("Цели › Ограничения")
    goal = _mreq(1, "Перевод доступен в любое время суток, в течение минуты.", section="Часть A › Цель")
    nfr = _mreq(2, "Перевод выполняется в течение минуты.")
    assert not is_measurable(goal) and is_measurable(nfr) and [r.rid for r in select([goal, nfr])] == ["R-002"]


# ---------- защищённые правила: единицы ----------

def test_units_finding_cannot_be_disputed_or_lowered_below_high():
    unit = _f("F-001", "ambiguous", "high", [_q(0), _q(50)], origin="rules.units")
    other = _f("F-002", "ambiguous", "high", [_q(100)])
    merged_into = _f("F-003", "incomplete", "medium", [_q(200)], origin="review")
    out = ReviewOutput(
        merges=[Merge(into="F-003", duplicates=["F-001"], reason="одно и то же")],
        severities=[SeverityChange(fid="F-003", severity="low", reason_code="defined_elsewhere", reason="очевидно")],
        disputed=[Disputed(fid="F-003", reason="очевидно из контекста"), Disputed(fid="F-002", reason="задано в таблице")])
    kept, log = apply_review([unit, other, merged_into], out, lambda rq: None)
    by = {f.fid: f for f in kept}
    assert by["F-003"].origin_pass == "review+rules.units" and is_protected(by["F-003"])
    assert by["F-003"].status == "confirmed" and by["F-003"].severity == "high"      # не оспорено, поднято до high
    assert log["dispute_rejected"][0]["fid"] == "F-003" and by["F-002"].status == "potential"
    lone, log2 = apply_review([unit], ReviewOutput(
        severities=[SeverityChange(fid="F-001", severity="medium", reason_code="formatting", reason="мелочь")],
        disputed=[Disputed(fid="F-001", reason="очевидно из контекста")]), lambda rq: None)
    assert (lone[0].status, lone[0].severity) == ("confirmed", "high") and log2["floors"][0]["fid"] == "F-001"


def test_review_run_never_returns_protected_fid_as_potential():
    class Fake:
        def complete(self, system, user, schema, stage):
            return ReviewOutput(disputed=[Disputed(fid="F-001", reason="очевидно"), Disputed(fid="F-002", reason="задано")])
    fs = [_f("F-001", "ambiguous", "high", [_q(0), _q(50)], origin="rules.units"), _f("F-002", "ambiguous", "high", [_q(100)])]
    assert review_run(Fake(), "[B-001] цитата", [req(1)], findings=fs).potential_fids == ["F-002"]
