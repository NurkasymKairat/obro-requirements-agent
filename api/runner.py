"""Запуск конвейера агента для веб-интерфейса: настоящий (WEB_PIPELINE=real) и заглушка (stub).

Своей логики анализа здесь нет: настоящий прогон — agent.pipeline.Pipeline, отчёт — agent.report.
Подкласс Pipeline только отмечает начало и конец каждого прохода для индикатора прогресса.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agent.config import load_settings
from agent.llm import make_client
from agent.pipeline import Pipeline, default_passes
from agent.reader import read_document
from agent.report import render, write_report
from agent.schemas import Document, RunResult

from .demo import load_demo


@dataclass
class Outcome:
    result: RunResult
    doc: Document
    report_md: str


class Progress:
    """Список проходов с отметками. Читается из потока запросов, пишется из потока прогона."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[dict] = []

    def plan(self, names: list[str]) -> None:
        with self._lock:
            self._items = [{"name": n, "status": "pending", "seconds": None, "calls": None, "cache_hits": None,
                            "error": None, "_t0": None} for n in names]

    def _get(self, name: str) -> dict:
        for it in self._items:
            if it["name"] == name:
                return it
        it = {"name": name, "status": "pending", "seconds": None, "calls": None, "cache_hits": None, "error": None,
              "_t0": None}
        self._items.append(it)
        return it

    def start(self, name: str) -> None:
        with self._lock:
            it = self._get(name)
            it["status"], it["_t0"] = "running", time.monotonic()

    def finish(self, stage: dict) -> None:
        with self._lock:
            it = self._get(stage["name"])
            it["status"] = stage.get("status", "ok")
            it["seconds"] = stage.get("seconds")
            it["calls"] = stage.get("calls")
            it["cache_hits"] = stage.get("cache_hits")
            it["error"] = stage.get("error")

    def sync(self, stages: list[dict]) -> None:
        """Итог по stages из RunResult: пропущенные проходы не проходят через _exec."""
        for s in stages:
            if s.get("status") == "skipped" or self._status(s["name"]) in ("pending", "running"):
                self.finish(s)

    def _status(self, name: str) -> str:
        with self._lock:
            return self._get(name)["status"]

    def snapshot(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            out = []
            for it in self._items:
                d = {k: v for k, v in it.items() if not k.startswith("_")}
                if it["status"] == "running" and it["_t0"] is not None:
                    d["seconds"] = round(now - it["_t0"], 1)
                out.append(d)
            return out


class TrackedPipeline(Pipeline):
    def __init__(self, llm, progress: Progress) -> None:
        super().__init__(llm)
        self.progress = progress

    def _exec(self, stage: dict, fn, check_stop: bool = True):
        self.progress.start(stage["name"])
        try:
            return super()._exec(stage, fn, check_stop)
        finally:
            self.progress.finish(stage)


def planned_names(passes: list, rules: bool = True) -> list[str]:
    return ["extract", *(["rules"] if rules else []), *(p.NAME for p in passes)]


def run_real(path: Path, display_name: str, progress: Progress) -> Outcome:
    started = time.monotonic()
    progress.plan(planned_names(default_passes()))   # до настроек: без ключа видно, какие проходы не выполнены
    llm = make_client(load_settings())       # ConfigError без ключа — прогон помечается как failed
    pipeline = TrackedPipeline(llm, progress)
    progress.plan(planned_names(pipeline.passes, pipeline.use_rules))
    result = pipeline.run(str(path))
    progress.sync(result.stages)
    doc = read_document(path)
    result.document = display_name
    with tempfile.TemporaryDirectory(prefix="agent-web-report-") as tmp:
        md, _ = write_report(result, Path(tmp) / "report.md", started)
        report_md = md.read_text(encoding="utf-8")
    return Outcome(result, doc, report_md)


def run_stub(path: Path, display_name: str, progress: Progress, incomplete: bool = False) -> Outcome:
    """Без модели: проигрывает проходы сохранённого примера с паузами и возвращает его результат.
    Документ — документ примера (позиции цитат относятся к нему), загруженный файл только проверяется."""
    demo = load_demo()
    stages = [dict(s) for s in demo.result.stages]
    progress.plan([s["name"] for s in stages])
    delay = float(os.getenv("WEB_STUB_DELAY", "1.0"))
    for i, s in enumerate(stages):
        if incomplete and i >= 3:
            s.update(status="failed" if i == 3 else "skipped", calls=0, seconds=0.0,
                     error="Нет доступных моделей: 429 RESOURCE_EXHAUSTED (заглушка)" if i == 3
                     else "пропущен: все модели недоступны (заглушка)")
            if i > 3:
                progress.finish(s)
                continue
        progress.start(s["name"])
        time.sleep(delay * (0.3 if s.get("seconds", 0) < 1 else 1.0))
        progress.finish(s)
    result = demo.result.model_copy(deep=True)
    if incomplete:
        bad = [s for s in stages if s["status"] != "ok"]
        result.stages = stages
        result.verdict = "incomplete"
        result.incomplete_reason = ("не выполнены проходы: " + ", ".join(
            f"{s['name']} ({'пропущен' if s['status'] == 'skipped' else 'сбой'})" for s in bad)
            + ". Причина остановки: все модели недоступны (заглушка)")
        return Outcome(result, demo.doc, render(result))
    return Outcome(result, demo.doc, demo.report_md)


def run_pipeline(path: Path, display_name: str, progress: Progress) -> Outcome:
    mode = os.getenv("WEB_PIPELINE", "real").strip().lower() or "real"
    if mode == "stub":
        return run_stub(path, display_name, progress)
    if mode == "stub-incomplete":
        return run_stub(path, display_name, progress, incomplete=True)
    return run_real(path, display_name, progress)
