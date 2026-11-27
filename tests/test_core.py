from pathlib import Path

import docx
import pymupdf

from agent.pipeline import Pipeline, compute_verdict
from agent.reader import read_document, render_for_llm
from agent.report import render
from agent.schemas import RawFinding, RawQuote, RawRequirement
from agent.verify import Verifier

FIX = Path(__file__).parent / "fixtures" / "sample.md"


def test_md_blocks_and_offsets():
    doc = read_document(FIX)
    for b in doc.blocks:
        assert doc.text[b.start:b.end] == b.text
    kinds = [b.kind for b in doc.blocks]
    assert "table_row" in kinds and "list_item" in kinds and "code" in kinds
    assert not any(":---" in b.text for b in doc.blocks)
    t1 = next(b for b in doc.blocks if "Т-1" in b.text)
    assert t1.section == "Часть 1. Описание › Требования"
    assert t1.line == 7 and "строка 7" in t1.location
    assert "[B-001]" in render_for_llm(doc)


def test_verify_normalization_and_fuzzy():
    doc = read_document(FIX)
    v = Verifier(doc)
    t1 = next(b for b in doc.blocks if "Т-1" in b.text)
    q, how = v.find(RawQuote(text='Т-1. Система начисляет "бонусы" - 1 % от суммы покупки.', block_id=t1.block_id))
    assert how == "exact" and q.block_id == t1.block_id
    assert doc.text[q.start:q.end] == q.text and "«бонусы»" in q.text
    q, how = v.find(RawQuote(text="Покупка 1 000 ₸ | Провести покупку | Начислено 10 бонусов", block_id="B-999"))
    assert q is not None and q.text.startswith("Покупка")
    q, how = v.find(RawQuote(text="Бонусы сгорают через 12 месяцов.", block_id=""))
    assert how == "fuzzy"
    q, how = v.find(RawQuote(text="Бонусы начисляются за переводы", block_id=t1.block_id))
    assert q is None
    q, _ = v.find(RawQuote(text="Т-1.", block_id=t1.block_id))      # разметка не режется пополам
    assert q.text == "**Т-1.**"


def test_docx_and_pdf(tmp_path):
    d = docx.Document()
    d.add_heading("Требования", 1)
    d.add_paragraph("Система хранит данные 5 лет.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "amount", "integer"
    d.save(tmp_path / "a.docx")
    doc = read_document(tmp_path / "a.docx")
    assert [b.kind for b in doc.blocks] == ["heading", "paragraph", "table_row"]
    assert all(doc.text[b.start:b.end] == b.text for b in doc.blocks)

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Requirement one shall hold.", fontsize=11)
    pdf.save(tmp_path / "a.pdf")
    doc = read_document(tmp_path / "a.pdf")
    assert doc.blocks[0].page == 1 and "стр. 1" in doc.blocks[0].location


class FakeLLM:
    name, model = "fake", "fake-1"

    def complete(self, system, user, schema, stage):
        return schema(requirements=[
            RawRequirement(source_id="Т-2.", section="Требования", summary="Срок сгорания",
                           quote=RawQuote(text="Бонусы сгорают через 12 месяцев.", block_id="B-005")),
            RawRequirement(source_id="Т-1", section="Требования", summary="Ставка начисления",
                           quote=RawQuote(text="Система начисляет «бонусы» — 1 % от суммы покупки.", block_id="B-004")),
            RawRequirement(source_id=None, section="Требования", summary="Выдумка",
                           quote=RawQuote(text="Бонусы можно передать другу.", block_id="B-004")),
        ])


class FakePass:
    NAME = "fake_check"

    @staticmethod
    def run(client, document_text, requirements):
        assert "[B-001]" in document_text and len(requirements) == 2
        return [
            RawFinding(scope="requirement", requirement_ids=["R-002", "R-999"], criterion="проверяемость",
                       standard="ISO/IEC/IEEE 29148 п. 5.2.5", kind="unverifiable", severity="critical",
                       description="Нет точки отсчёта срока.", fix="Указать точку отсчёта.",
                       quotes=[RawQuote(text="через 12 месяцев", block_id="B-005")]),
            RawFinding(scope="set", requirement_ids=[], criterion="согласованность", standard="п. 5.2.6",
                       kind="contradiction", severity="high", description="Выдуманное противоречие",
                       fix="-", quotes=[RawQuote(text="через 12 месяцев", block_id="B-005"),
                                        RawQuote(text="через 6 месяцев", block_id="B-005")]),
        ]


def test_pipeline_end_to_end_offline():
    res = Pipeline(FakeLLM(), passes=[FakePass()], rules=False).run(str(FIX))
    assert [r.rid for r in res.requirements] == ["R-001", "R-002"]
    assert res.requirements[0].source_id == "Т-1"           # порядок по документу, точка срезана
    assert len(res.stages[0]["dropped"]) == 1               # выдуманная цитата отброшена
    assert len(res.findings) == 1 and res.findings[0].requirement_ids == ["R-002"]
    assert len(res.stages[1]["dropped"]) == 1               # противоречие без второй стороны отброшено
    assert res.verdict == "not_ready" == compute_verdict(res.findings)
    md = render(res)
    for h in ["## 1. Паспорт", "## 2. Нарезка", "## 3. Проверка каждого", "## 4. Проверка набора",
              "## 5. Сводка", "## 6. Итог", "## Главное"]:
        assert h in md
    assert md.index("## 1.") < md.index("## Главное") < md.index("## 2.")
    main = md[md.index("## Главное"):md.index("## 2.")]
    for part in ("Цитата", "Место:", "Критерий:", "стандарт:", "Как исправить:", "F-001"):
        assert part in main
    assert "Вызовов модели" in md and "| Время |" in md
    assert "F-001" in md and "через 12 месяцев" in md


def test_pipeline_extract_only():
    res = Pipeline(FakeLLM(), passes=[], rules=False).run(str(FIX))
    md = render(res)
    assert "не выполнялись" in md and "предварительный" in md


def test_pipeline_with_rules_does_not_crash():
    res = Pipeline(FakeLLM(), passes=[]).run(str(FIX))
    assert res.stages[1]["name"] == "rules" and res.stages[1]["status"] == "ok"
    assert all(f.fid.startswith("F-") for f in res.findings)


class FakeChallenge:
    NAME = "fake_challenge"

    @staticmethod
    def run(client, document_text, requirements, findings):
        from agent.schemas import PassResult
        assert [f.fid for f in findings] == ["F-001"]
        return PassResult(potential_fids=[findings[0].fid])


def test_pass_receives_findings_and_marks_potential():
    res = Pipeline(FakeLLM(), passes=[FakePass(), FakeChallenge()], rules=False).run(str(FIX))
    assert res.findings[0].status == "potential"
    assert res.verdict == "ready"            # оспоренное critical не блокирует
    assert res.stages[-1]["potential"] == 1


def _v():
    doc = read_document(FIX)
    return doc, Verifier(doc)


def test_quote_keeps_closing_guillemet_and_sentence_end():
    doc, v = _v()
    q, _ = v.find(RawQuote(text="Приложение берёт ставки из документа «Справочник тарифов — версия 2", block_id=""))
    assert q.text == "Приложение берёт ставки из документа «Справочник тарифов — версия 2»."
    assert doc.text[q.start:q.end] == q.text


def test_quote_keeps_closing_parenthesis():
    _, v = _v()
    q, _ = v.find(RawQuote(text="Срок хранения (в днях, не меньше 30", block_id=""))
    assert q.text == "Срок хранения (в днях, не меньше 30)"
    q, _ = v.find(RawQuote(text="не меньше 30) задаёт", block_id=""))   # закрывающая без открывающей
    assert q.text == "(в днях, не меньше 30) задаёт"


def test_requirement_quote_extends_to_sentence_or_cell_boundary():
    _, v = _v()
    q, _ = v.find(RawQuote(text="Приложение берёт ставки", block_id=""), whole_sentence=True)
    assert q.text == "Приложение берёт ставки из документа «Справочник тарифов — версия 2»."
    q, _ = v.find(RawQuote(text="Срок хранения (в днях", block_id=""), whole_sentence=True)
    assert q.text == "Срок хранения (в днях, не меньше 30) задаёт администратор."
    q, _ = v.find(RawQuote(text="Покупка 1 000 ₸ | Провести", block_id=""), whole_sentence=True)
    assert q.text == "Покупка 1 000 ₸ | Провести покупку"                # граница ячейки


def test_finding_fragment_stays_fragment_and_wrapper_removed():
    _, v = _v()
    q, _ = v.find(RawQuote(text="ставки из документа", block_id=""))
    assert q.text == "ставки из документа"
    q, _ = v.find(RawQuote(text='"Бонусы сгорают через 12 месяцев."', block_id=""))
    assert q.text == "Бонусы сгорают через 12 месяцев."


class BrokePass:
    NAME = "broke"

    @staticmethod
    def run(client, document_text, requirements):
        raise RuntimeError("модель вернула мусор")


class BalancePass:
    NAME = "balance"

    @staticmethod
    def run(client, document_text, requirements):
        from agent.llm.base import BalanceExhausted
        raise BalanceExhausted("402: закончился баланс")


class AfterAll:
    """Зависимый проход (принимает findings): идёт отдельной группой после независимых."""
    NAME = "after_all"

    @staticmethod
    def run(client, document_text, requirements, findings=()):
        return []


def test_failed_pass_makes_run_incomplete_without_verdict():
    # FakePass, BalancePass, BrokePass — одна параллельная группа: выполняются все; AfterAll — пропущен
    p = Pipeline(FakeLLM(), passes=[FakePass(), BalancePass(), BrokePass(), AfterAll()], rules=False)
    res = p.run(str(FIX))
    st = {s["name"]: s["status"] for s in res.stages}
    assert st == {"extract": "ok", "fake_check": "ok", "balance": "failed", "broke": "failed",
                  "after_all": "skipped"}
    assert res.verdict == "incomplete" and "balance" in res.incomplete_reason and "402" in res.incomplete_reason
    assert [s["name"] for s in res.stages] == ["extract", "fake_check", "balance", "broke", "after_all"]
    assert res.findings                                   # уже найденное не теряется
    md = render(res)
    assert "**НЕПОЛНЫЙ**" in md and "Вердикт не выносится" in md and "**пропущен**" in md


def test_other_failure_continues_and_marks_incomplete():
    res = Pipeline(FakeLLM(), passes=[BrokePass(), FakePass()], rules=False).run(str(FIX))
    assert [s["status"] for s in res.stages] == ["ok", "failed", "ok"]
    assert res.verdict == "incomplete"


def test_json_written_before_markdown(tmp_path, monkeypatch):
    import agent.report as rep

    res = Pipeline(FakeLLM(), passes=[], rules=False).run(str(FIX))
    monkeypatch.setattr(rep, "render", lambda r: (_ for _ in ()).throw(RuntimeError("шаблон сломан")))
    import pytest
    with pytest.raises(RuntimeError):
        rep.write_report(res, tmp_path / "r.md")
    assert (tmp_path / "r.json").is_file() and not (tmp_path / "r.md").exists()


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    from agent import __main__ as cli
    from agent.llm.base import AllModelsUnavailable, BalanceExhausted

    def stub(exc):
        class P:
            NAME = "p"

            @staticmethod
            def run(client, document_text, requirements):
                raise exc
        return P

    monkeypatch.setattr(cli, "make_client", lambda s: FakeLLM())
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    cases = [(BalanceExhausted("закончился баланс"), 5), (AllModelsUnavailable("Все модели недоступны"), 3),
             (RuntimeError("сбой"), 4), (None, 0)]
    for exc, code in cases:
        passes = [stub(exc)] if exc else []
        monkeypatch.setattr(cli, "Pipeline", lambda llm, passes=passes: Pipeline(llm, passes=passes, rules=False))
        out = tmp_path / f"r{code}.md"
        assert cli.main([str(FIX), str(out)]) == code
        assert out.is_file() and out.with_suffix(".json").is_file()      # отчёт пишется и при сбое
        err = capsys.readouterr().err
        assert "Traceback" not in err
        if code == 5:
            assert "баланс" in err


class ModeLLM(FakeLLM):
    """Записывает режим рассуждения, в котором вызван каждый проход."""

    def __init__(self):
        from contextlib import contextmanager
        from contextvars import ContextVar
        import threading
        self._var, self.seen, self._lock = ContextVar("mode", default="on"), [], threading.Lock()

        @contextmanager
        def using_pass(thinking=None, max_output_tokens=None):
            token = self._var.set(thinking or self._var.get())   # как в настоящем клиенте: своё у каждого потока
            yield
            self._var.reset(token)
        self.using_pass = using_pass

    @property
    def thinking(self):
        return self._var.get()

    def complete(self, system, user, schema, stage):
        with self._lock:
            self.seen.append((stage, self.thinking))
        return super().complete(system, user, schema, stage)


def _probe(name, attr=None):
    class P:
        NAME = name

        @staticmethod
        def run(client, document_text, requirements):
            with client._lock:
                client.seen.append((name, client.thinking))
            return []
    if attr:
        P.THINKING = attr
    return P


def test_thinking_mode_per_pass():
    from agent.pipeline import DEFAULT_THINKING

    llm = ModeLLM()
    passes = [_probe("individual"), _probe("cross_acceptance"), _probe("cross_parts"), _probe("review"),
              _probe("states_units"), _probe("cross_parts_cheap", "off")]
    res = Pipeline(llm, passes=passes, rules=False).run(str(FIX))
    # review всегда последним
    # проходы одной группы идут параллельно: порядок прихода не важен, важен уровень каждого прохода
    assert dict(llm.seen) == {"extract": "off", "individual": "off", "cross_acceptance": "high", "cross_parts": "high",
                              "states_units": "high", "cross_parts_cheap": "off", "review": "high"}
    assert llm.seen[0] == ("extract", "off")
    # заглушка review без параметра findings независима и идёт в общей группе; в отчёте она всё равно последняя
    assert [s["name"] for s in res.stages][-1] == "review"
    assert [s["thinking"] for s in res.stages] == ["off", "off", "high", "high", "high", "off", "high"]
    assert llm.thinking == "on"                                     # режим возвращён после прохода
    assert DEFAULT_THINKING["extract"] == "off" and DEFAULT_THINKING["review"] == "high"
    assert "| off |" in render(res) or "| off" in render(res)
