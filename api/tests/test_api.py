"""Тесты API без вызова модели: пример, заглушка конвейера, ограничения загрузки."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.config import ConfigError  # noqa: E402
from api import runner  # noqa: E402
from api.main import app, store  # noqa: E402

client = TestClient(app)


def wait(run_id: str, timeout: float = 15) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.05)
    raise AssertionError("прогон не завершился")


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setenv("WEB_PIPELINE", "stub")
    monkeypatch.setenv("WEB_STUB_DELAY", "0.01")
    yield
    while store.busy():
        time.sleep(0.02)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_demo_positions_match_document():
    j = client.get("/api/demo").json()
    text = j["document"]["text"]
    assert j["mismatched_quotes"] == 0
    for f in j["result"]["findings"]:
        for q in f["quotes"]:
            assert text[q["start"]:q["end"]] == q["text"]
    for b in j["document"]["blocks"]:
        for c in b.get("cells", []):
            assert b["start"] <= c["start"] <= c["end"] <= b["end"]
            assert "|" not in text[c["start"]:c["end"]].replace("\\|", "")
    assert set(j["view"]["main_fids"]) <= {f["fid"] for f in j["result"]["findings"]}


def test_demo_report():
    r = client.get("/api/demo/report.md")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("#")


def test_upload_run_and_report(tmp_path):
    r = client.post("/api/runs", files={"file": ("ТЗ.md", b"# test\n\ntext\n", "text/markdown")})
    assert r.status_code == 202
    run_id = r.json()["id"]
    j = wait(run_id)
    assert j["status"] == "done"
    assert j["result"] and j["document"] and j["view"]
    assert all(s["status"] == "ok" for s in j["stages"])
    rep = client.get(f"/api/runs/{run_id}/report.md")
    assert rep.status_code == 200 and len(rep.text) > 100


def test_one_run_at_a_time(monkeypatch):
    monkeypatch.setenv("WEB_STUB_DELAY", "0.3")
    a = client.post("/api/runs", files={"file": ("a.md", b"# a\n", "text/markdown")})
    b = client.post("/api/runs", files={"file": ("b.md", b"# b\n", "text/markdown")})
    assert a.status_code == 202 and b.status_code == 409
    wait(a.json()["id"])


def test_rejects_bad_files():
    assert client.post("/api/runs", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 415
    assert client.post("/api/runs", files={"file": ("x.md", b"", "text/markdown")}).status_code == 400
    big = b"a" * (10 * 1024 * 1024 + 1)
    assert client.post("/api/runs", files={"file": ("x.txt", big, "text/plain")}).status_code == 413


def test_upload_is_deleted_after_run(monkeypatch):
    seen = {}

    def fake(path, name, progress):
        seen["path"] = path
        assert path.is_file()
        return runner.run_stub(path, name, progress)

    monkeypatch.setattr("api.runs.run_pipeline", fake)
    j = wait(client.post("/api/runs", files={"file": ("a.md", b"# a\n", "text/markdown")}).json()["id"])
    assert j["status"] == "done"
    assert not seen["path"].exists() and not seen["path"].parent.exists()


def test_incomplete_run_has_no_verdict(monkeypatch):
    monkeypatch.setenv("WEB_PIPELINE", "stub-incomplete")
    j = wait(client.post("/api/runs", files={"file": ("a.md", b"# a\n", "text/markdown")}).json()["id"])
    assert j["status"] == "done"
    assert j["result"]["verdict"] == "incomplete" and j["result"]["incomplete_reason"]
    assert j["view"]["failed_stages"]


def test_missing_key_fails_cleanly(monkeypatch):
    monkeypatch.setenv("WEB_PIPELINE", "real")
    monkeypatch.setenv("GEMINI_API_KEY", "FAKEKEYTESTSECRET1234567890")

    def no_key():
        raise ConfigError("Не задан ключ FAKEKEYTESTSECRET1234567890")

    monkeypatch.setattr(runner, "load_settings", no_key)
    j = wait(client.post("/api/runs", files={"file": ("a.md", b"# a\n", "text/markdown")}).json()["id"])
    assert j["status"] == "failed" and "Настройки агента" in j["error"]
    assert "FAKEKEYTESTSECRET" not in str(j)          # ключ из окружения не уходит во фронт
    assert j["stages"] and all(s["status"] == "pending" for s in j["stages"])
