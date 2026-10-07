from pathlib import Path

import pytest

from datapipe.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
SCHEMA = EX / "schema_sales.json"
ANALYSIS = EX / "analysis_sales.json"
CLEAN_CSV = (EX / "sales.csv").read_text()


@pytest.fixture(autouse=True)
def _private_config_dir(tmp_path, monkeypatch):
    """No test may read or write the developer's real per-user folder (where the saved LLM key lives), their Windows Credential Manager,
    or pick up a key from their environment."""
    monkeypatch.setenv("DATAPIPE_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.delenv("DATAPIPE_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DATAPIPE_KEY_STORE", "file")          # the Windows Credential Manager is the real one: only tests that ask for it touch it


@pytest.fixture
def wd(tmp_path):
    return tmp_path / "work"


@pytest.fixture
def write(tmp_path):
    def _write(name, content, mode="w"):
        p = tmp_path / name
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        return p
    return _write


@pytest.fixture
def run(wd):
    def _run(path, policy="low", schema=SCHEMA, analysis=ANALYSIS, actor="alice", **kw):
        return run_pipeline(path, workdir=wd, policy_name=policy,
                            schema_path=schema, analysis_path=analysis, actor=actor, **kw)
    return _run
