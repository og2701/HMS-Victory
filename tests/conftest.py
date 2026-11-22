"""Shared test setup.

Vic's reply brain (lib/features/vic_brain.py) is switched off by default so the responder tests keep exercising
the path they were written for, and its files (thread memory, run log) go to a temp folder rather than data/json.
Tests of the brain itself turn it on.
"""
import pytest


@pytest.fixture(autouse=True)
def _vic_brain_sandbox(tmp_path, monkeypatch):
    from lib.features import vic_brain
    monkeypatch.setattr(vic_brain, "ENABLED", False)
    monkeypatch.setattr(vic_brain, "MEMORY_FILE", str(tmp_path / "vic_thread_memory.json"))
    monkeypatch.setattr(vic_brain, "RUN_LOG_FILE", str(tmp_path / "vic_t2_runs.jsonl"))
    monkeypatch.setattr(vic_brain, "_state", None)
    yield
