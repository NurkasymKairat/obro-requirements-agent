"""Версия агента: git describe, а без git — коммит из APP_COMMIT (сборка образа)."""
import subprocess

from agent import __version__
from agent import pipeline


def _no_git(*a, **kw):
    raise OSError("git not found")


def test_without_git_uses_app_commit(monkeypatch):
    monkeypatch.setattr(pipeline.subprocess, "run", _no_git)
    monkeypatch.setenv("APP_COMMIT", "abc1234")
    assert pipeline.agent_version() == f"{__version__}+abc1234"


def test_without_git_and_commit_is_plain_version(monkeypatch):
    monkeypatch.setattr(pipeline.subprocess, "run", _no_git)
    monkeypatch.delenv("APP_COMMIT", raising=False)
    assert pipeline.agent_version() == __version__


def test_empty_git_output_falls_back_to_app_commit(monkeypatch):
    monkeypatch.setattr(pipeline.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 128, stdout="", stderr="not a repo"))
    monkeypatch.setenv("APP_COMMIT", " def5678 ")
    assert pipeline.agent_version() == f"{__version__}+def5678"


def test_git_wins_over_app_commit(monkeypatch):
    monkeypatch.setattr(pipeline.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="1111111\n", stderr=""))
    monkeypatch.setenv("APP_COMMIT", "abc1234")
    assert pipeline.agent_version() == f"{__version__}+1111111"
