"""Готовый пример: документ и сохранённый результат прогона по нему, без вызова модели.

По умолчанию — api/demo/sample_other.md и api/demo/sample.json (+ sample.md рядом).
Переопределяется переменными WEB_DEMO_DOC и WEB_DEMO_RESULT.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agent.reader import read_document
from agent.report import render
from agent.schemas import Document, RunResult

from .docview import check_positions

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = Path(__file__).resolve().parent / "demo"
log = logging.getLogger("api.demo")


@dataclass
class Demo:
    result: RunResult
    doc: Document
    report_md: str
    mismatched: int          # цитат, не совпавших с документом по позициям
    doc_name: str


def _path(env: str, default: Path) -> Path:
    raw = os.getenv(env, "").strip()
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def demo_paths() -> tuple[Path, Path]:
    doc = _path("WEB_DEMO_DOC", DEMO_DIR / "sample_other.md")
    res = _path("WEB_DEMO_RESULT", DEMO_DIR / "sample.json")
    return doc, res


@lru_cache(maxsize=4)
def _load(doc_path: Path, res_path: Path, mtimes: tuple) -> Demo:
    result = RunResult.model_validate(json.loads(res_path.read_text(encoding="utf-8")))
    doc = read_document(doc_path)
    md_path = res_path.with_suffix(".md")
    report_md = md_path.read_text(encoding="utf-8") if md_path.is_file() else render(result)
    bad = check_positions(result, doc)
    if bad:
        log.warning("Пример: %d цитат не совпадают с %s по позициям — результат от другого документа?", bad, doc_path)
    return Demo(result, doc, report_md, bad, doc_path.name)


def load_demo() -> Demo:
    doc, res = demo_paths()
    for p, env in ((doc, "WEB_DEMO_DOC"), (res, "WEB_DEMO_RESULT")):
        if not p.is_file():
            raise FileNotFoundError(f"Пример не найден: {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p} "
                                    f"(переменная {env})")
    return _load(doc, res, (doc.stat().st_mtime, res.stat().st_mtime))
