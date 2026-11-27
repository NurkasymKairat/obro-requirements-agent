"""HTTP API веб-интерфейса и раздача собранного фронта (web/dist)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent.pipeline import agent_version

from .demo import load_demo
from .docview import result_payload
from .runs import Busy, RunStore

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"
MAX_BYTES = 10 * 1024 * 1024
ALLOWED = (".pdf", ".md", ".markdown", ".txt", ".docx")
SECRET_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY")

app = FastAPI(title="Агент проверки требований", docs_url="/api/docs", openapi_url="/api/openapi.json")
store = RunStore()


def _scrub(obj):
    """Ключи никогда не уходят во фронт, даже если попали в текст ошибки провайдера."""
    secrets = [v for k in SECRET_ENV if len(v := os.getenv(k, "").strip()) >= 8]
    if not secrets:
        return obj
    raw = json.dumps(obj, ensure_ascii=False)
    for s in secrets:
        raw = raw.replace(s, "***")
    return json.loads(raw)


def _json(obj, status: int = 200) -> JSONResponse:
    return JSONResponse(_scrub(obj), status_code=status)


def _md(text: str, name: str) -> Response:
    stem = Path(name).stem or "report"
    fname = f"report-{stem}.md"
    return Response(_scrub(text), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=\"report.md\"; "
                                                    f"filename*=UTF-8''{quote(fname)}"})


@app.get("/api/health")
def health():
    return {"status": "ok", "title": os.getenv("WEB_TITLE", "").strip() or "Проверка требований",
            "version": agent_version(), "busy": store.busy(),
            "pipeline": os.getenv("WEB_PIPELINE", "real").strip().lower() or "real"}


@app.get("/api/demo")
def demo():
    try:
        d = load_demo()
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    payload = result_payload(d.result, d.doc)
    return _json({"id": "demo", "filename": d.doc_name, "status": "done", "error": None, "current": None,
                  "stages": [], "progress": {"done": 0, "total": 0}, "elapsed": 0.0,
                  "mismatched_quotes": d.mismatched, **payload})


@app.get("/api/demo/report.md")
def demo_report():
    d = load_demo()
    return _md(d.report_md, d.doc_name)


@app.post("/api/runs", status_code=202)
async def create_run(file: UploadFile, request: Request):
    name = Path(file.filename or "").name
    if not name.lower().endswith(ALLOWED):
        raise HTTPException(415, "Нужен файл PDF, Markdown, TXT или DOCX.")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ.")
    if not data:
        raise HTTPException(400, "Файл пустой.")
    try:
        run = store.start(name, data)
    except Busy as e:
        raise HTTPException(409, str(e))
    return {"id": run.id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = store.get(run_id)
    if run is None:
        raise HTTPException(404, "Прогон не найден. Состояние хранится в памяти и сбрасывается при перезапуске сервера.")
    return _json(run.view())


@app.get("/api/runs/{run_id}/report.md")
def get_report(run_id: str):
    run = store.get(run_id)
    if run is None:
        raise HTTPException(404, "Прогон не найден.")
    if run.status != "done" or run.outcome is None:
        raise HTTPException(409, "Отчёт ещё не готов.")
    return _md(run.outcome.report_md, run.filename)


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
else:
    @app.get("/")
    def no_frontend():
        return Response("Фронт не собран: выполните `cd web && npm ci && npm run build` "
                        "или откройте режим разработки http://localhost:5173.",
                        media_type="text/plain; charset=utf-8")
