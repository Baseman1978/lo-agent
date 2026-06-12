"""Config-validatie op de systeemgrens."""

import pytest

from span.config import load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in [
        "ORQ_API_KEY", "ORQ_BASE_URL", "NEO4J_PASSWORD", "SPAN_EMBED_DIMS",
        "WORK_NEO4J_URI", "BRAIN_DB",
    ]:
        monkeypatch.delenv(var, raising=False)
    # voorkom dat een echte .env meelift
    monkeypatch.setattr("span.config.PROJECT_ROOT", tmp_path)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    with pytest.raises(RuntimeError, match="ORQ_API_KEY"):
        load_settings()


def test_missing_neo4j_password_raises(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
        load_settings()


def test_defaults(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    s = load_settings()
    assert s.orq_base_url == "https://api.orq.ai/v3/router"
    assert s.brain_db == "span-brain"
    assert s.embed_dims == 1024
    assert s.work is None


def test_invalid_embed_dims(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("SPAN_EMBED_DIMS", "veel")
    with pytest.raises(RuntimeError, match="SPAN_EMBED_DIMS"):
        load_settings()


def test_embed_dims_out_of_range(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("SPAN_EMBED_DIMS", "9999")
    with pytest.raises(RuntimeError, match="buiten bereik"):
        load_settings()


def test_work_db_optional(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("WORK_NEO4J_URI", "bolt://prod:7687")
    s = load_settings()
    assert s.work is not None
    assert s.work.uri == "bolt://prod:7687"
