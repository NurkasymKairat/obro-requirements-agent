"""Прогоны в памяти. Один прогон одновременно; загруженный файл удаляется после прогона."""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import ConfigError

from .docview import result_payload
from .runner import Outcome, Progress, run_pipeline

log = logging.getLogger("api.runs")
KEEP = 20   # сколько завершённых прогонов держать в памяти


class Busy(RuntimeError):
    pass


@dataclass
class Run:
    id: str
    filename: str
    size: int = 0                   # байт, для экрана прогресса
    status: str = "queued"          # queued | running | done | failed
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    progress: Progress = field(default_factory=Progress)
    outcome: Outcome | None = None
    _payload: dict | None = None

    def view(self) -> dict:
        stages = self.progress.snapshot()
        current = next((s["name"] for s in stages if s["status"] == "running"), None)
        done = sum(1 for s in stages if s["status"] not in ("pending", "running"))
        end = self.finished or time.time()
        out = {
            "id": self.id, "filename": self.filename, "size": self.size, "status": self.status, "error": self.error,
            "current": current, "stages": stages,
            "progress": {"done": done, "total": len(stages)},
            "elapsed": round(end - self.started, 1) if self.started else 0.0,
        }
        if self.status == "done" and self.outcome is not None:
            if self._payload is None:
                self._payload = result_payload(self.outcome.result, self.outcome.doc)
            out.update(self._payload)
        return out


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._slot = threading.Lock()      # занят, пока идёт прогон
        self._lock = threading.Lock()

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def busy(self) -> bool:
        return self._slot.locked()

    def start(self, filename: str, data: bytes) -> Run:
        if not self._slot.acquire(blocking=False):
            raise Busy("Уже идёт прогон. Дождитесь его окончания.")
        try:
            tmp = Path(tempfile.mkdtemp(prefix="agent-web-upload-"))
            path = tmp / ("upload" + Path(filename).suffix.lower())
            path.write_bytes(data)
            run = Run(id=uuid.uuid4().hex[:12], filename=filename, size=len(data))
            with self._lock:
                self._runs[run.id] = run
                self._trim()
            threading.Thread(target=self._work, args=(run, path, tmp), daemon=True, name=f"run-{run.id}").start()
            return run
        except BaseException:
            self._slot.release()
            raise

    def _work(self, run: Run, path: Path, tmp: Path) -> None:
        run.status, run.started = "running", time.time()
        try:
            run.outcome = run_pipeline(path, run.filename, run.progress)
            run.status = "done"
        except ConfigError as e:
            run.status, run.error = "failed", f"Настройки агента: {e}"
        except (ValueError, FileNotFoundError) as e:
            run.status, run.error = "failed", str(e)
        except Exception as e:  # noqa: BLE001 — пользователь видит причину, стектрейс уходит в лог сервера
            log.error("Прогон %s упал:\n%s", run.id, traceback.format_exc())
            run.status, run.error = "failed", f"Внутренняя ошибка агента: {type(e).__name__}: {e}"
        finally:
            run.finished = time.time()
            shutil.rmtree(tmp, ignore_errors=True)
            self._slot.release()

    def _trim(self) -> None:
        finished = sorted((r for r in self._runs.values() if r.status in ("done", "failed")), key=lambda r: r.created)
        for r in finished[:max(0, len(self._runs) - KEEP)]:
            self._runs.pop(r.id, None)
