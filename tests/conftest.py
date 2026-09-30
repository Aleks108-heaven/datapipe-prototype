from pathlib import Path

import pytest

from datapipe.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
SCHEMA = EX / "schema_sales.json"
ANALYSIS = EX / "analysis_sales.json"
CLEAN_CSV = (EX / "sales.csv").read_text()


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
