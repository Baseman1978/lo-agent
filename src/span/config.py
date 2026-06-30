"""Configuratie vanuit omgeving / .env — gevalideerd op de systeemgrens."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WorkDbConfig:
    """Optionele alleen-lezen koppeling naar productiedata."""

    uri: str
    user: str
    password: str
    database: str


# Microsofts eigen voorgeregistreerde publieke client ("Microsoft Graph
# Command Line Tools") — device code flow werkt hiermee zonder eigen
# app-registratie, mits de tenant het toestaat.
MS_PUBLIC_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"


@dataclass(frozen=True)
class JarvisConfig:
    """Optionele JARVIS-integraties: O365 (Microsoft Graph), Asana, Fireflies,
    Telegram. Eén bron voor alle integratie-config — geen losse env-reads
    verspreid over app.py/cli.py."""

    ms_client_id: str = MS_PUBLIC_CLIENT_ID
    ms_tenant_id: str = "common"
    # Client-secret: aanwezig => confidential web-app (OIDC auth-code login in de
    # browser). Leeg => public client (device-code flow, zoals voorheen).
    ms_client_secret: str = ""
    asana_token: str = ""
    asana_workspace: str = ""
    fireflies_api_key: str = ""
    telegram_bot_token: str = ""

    @property
    def o365_enabled(self) -> bool:
        return bool(self.ms_client_id)

    @property
    def web_login_enabled(self) -> bool:
        """True wanneer een client-secret is gezet: dan doet Span de Microsoft
        OIDC auth-code login in de browser i.p.v. de device-code flow."""
        return bool(self.ms_client_secret)

    @property
    def asana_enabled(self) -> bool:
        return bool(self.asana_token)

    @property
    def fireflies_enabled(self) -> bool:
        return bool(self.fireflies_api_key)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)


@dataclass(frozen=True)
class Settings:
    orq_api_key: str
    orq_base_url: str
    model_main: str
    model_light: str
    embed_model: str
    embed_dims: int
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    brain_db: str
    work: WorkDbConfig | None = field(default=None)
    jarvis: JarvisConfig = field(default_factory=JarvisConfig)
    # geheugen-verval: "off" (default, pure cosine), "soft" (zacht herordenen),
    # "log" (zacht + log welke fragmenten zouden stijgen/zakken)
    decay_mode: str = "off"
    # merknaam van de agent (één bron; wijzig via AGENT_NAME/AGENT_TAGLINE in .env)
    agent_name: str = "LO"
    agent_tagline: str = "DE AI-ASSISTENT VAN LOMANS"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Omgevingsvariabele {name} ontbreekt. Kopieer .env.example naar .env en vul aan."
        )
    return value


def _decay_mode() -> str:
    """SPAN_DECAY genormaliseerd, één keer gelezen (M21): off|soft|log."""
    mode = os.environ.get("SPAN_DECAY", "off").strip().lower()
    return mode if mode in {"off", "soft", "log"} else "off"


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env")

    embed_dims_raw = os.environ.get("SPAN_EMBED_DIMS", "1024").strip()
    try:
        embed_dims = int(embed_dims_raw)
    except ValueError as exc:
        raise RuntimeError(f"SPAN_EMBED_DIMS moet een getal zijn, kreeg: {embed_dims_raw!r}") from exc
    if not 64 <= embed_dims <= 4096:
        raise RuntimeError(f"SPAN_EMBED_DIMS buiten bereik 64..4096: {embed_dims}")

    work: WorkDbConfig | None = None
    work_uri = os.environ.get("WORK_NEO4J_URI", "").strip()
    if work_uri:
        work = WorkDbConfig(
            uri=work_uri,
            user=os.environ.get("WORK_NEO4J_USER", "neo4j").strip(),
            password=os.environ.get("WORK_NEO4J_PASSWORD", "").strip(),
            database=os.environ.get("WORK_DB", "neo4j").strip(),
        )

    return Settings(
        orq_api_key=_require("ORQ_API_KEY"),
        orq_base_url=os.environ.get("ORQ_BASE_URL", "https://api.orq.ai/v3/router").strip(),
        model_main=os.environ.get(
            "SPAN_MODEL_MAIN", "aws/eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ).strip(),
        model_light=os.environ.get(
            "SPAN_MODEL_LIGHT", "aws/eu.anthropic.claude-haiku-4-5-20251001-v1:0"
        ).strip(),
        embed_model=os.environ.get("SPAN_EMBED_MODEL", "openai/text-embedding-3-large").strip(),
        embed_dims=embed_dims,
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687").strip(),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j").strip(),
        neo4j_password=_require("NEO4J_PASSWORD"),
        brain_db=os.environ.get("BRAIN_DB", "span-brain").strip(),
        work=work,
        decay_mode=_decay_mode(),
        agent_name=os.environ.get("AGENT_NAME", "LO").strip() or "LO",
        agent_tagline=os.environ.get("AGENT_TAGLINE", "DE AI-ASSISTENT VAN LOMANS").strip(),
        jarvis=JarvisConfig(
            ms_client_id=os.environ.get("MS_CLIENT_ID", "").strip() or MS_PUBLIC_CLIENT_ID,
            ms_tenant_id=os.environ.get("MS_TENANT_ID", "common").strip() or "common",
            ms_client_secret=os.environ.get("MS_CLIENT_SECRET", "").strip(),
            asana_token=os.environ.get("ASANA_TOKEN", "").strip(),
            asana_workspace=os.environ.get("ASANA_WORKSPACE", "").strip(),
            fireflies_api_key=os.environ.get("FIREFLIES_API_KEY", "").strip(),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        ),
    )
