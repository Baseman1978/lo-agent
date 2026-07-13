"""Config-validatie op de systeemgrens."""

import pytest

from span.config import load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in [
        "ORQ_API_KEY", "ORQ_BASE_URL", "NEO4J_PASSWORD", "SPAN_EMBED_DIMS",
        "WORK_NEO4J_URI", "BRAIN_DB", "ASANA_TOKEN", "FIREFLIES_API_KEY",
        "TELEGRAM_BOT_TOKEN", "MS_CLIENT_ID",
        "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET", "WHATSAPP_ALLOWED_NUMBERS", "WHATSAPP_VOICE_REPLY",
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


def test_integraties_default_uit(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    for var in ("ASANA_TOKEN", "FIREFLIES_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    j = load_settings().jarvis
    assert j.o365_enabled  # publieke client-id default aan
    assert not j.asana_enabled
    assert not j.fireflies_enabled
    assert not j.telegram_enabled


def test_integraties_uit_env(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("FIREFLIES_API_KEY", "ff-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    j = load_settings().jarvis
    assert j.fireflies_enabled and j.fireflies_api_key == "ff-key"
    assert j.telegram_enabled and j.telegram_bot_token == "tg-token"


def test_build_integrations_geeft_drietal(monkeypatch):
    from span.integrations import build_integrations
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    for var in ("ASANA_TOKEN", "FIREFLIES_API_KEY", "MS_CLIENT_ID"):
        monkeypatch.delenv(var, raising=False)
    o365, asana, fireflies = build_integrations(load_settings())
    assert o365 is not None  # publieke client-id
    assert asana is None and fireflies is None  # niet geconfigureerd


def test_whatsapp_default_uit(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    j = load_settings().jarvis
    assert not j.whatsapp_enabled
    assert j.whatsapp_allowlist == frozenset()
    assert not j.whatsapp_voice_reply


def test_whatsapp_config_volledig(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "12345")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    monkeypatch.setenv("WHATSAPP_ALLOWED_NUMBERS", "+31 6 12345678, 31687654321")
    monkeypatch.setenv("WHATSAPP_VOICE_REPLY", "1")
    j = load_settings().jarvis
    assert j.whatsapp_enabled
    assert j.whatsapp_allowlist == frozenset({"31612345678", "31687654321"})
    assert j.whatsapp_voice_reply


def test_whatsapp_zonder_allowlist_blijft_uit(monkeypatch):
    """Fail-closed: zonder toegestaan nummer gaat het kanaal niet aan."""
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "12345")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    j = load_settings().jarvis
    assert not j.whatsapp_enabled
