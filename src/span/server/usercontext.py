"""Per-user context — fundament voor multi-user (WP-2/3).

Elke ingelogde gebruiker (Entra `oid`) krijgt een eigen privé-brein
(`brain-<oid>`) plus toegang tot het gedeelde brein (`brain-shared`). Deze
module levert de bouwstenen; het inweven in de endpoints gebeurt stapsgewijs
zodat de live single-user-flow blijft werken.

Neo4j Enterprise (al in gebruik) ondersteunt database-per-gebruiker via
`CREATE DATABASE`. De db-naam is afgeleid van de oid en gevalideerd op Neo4j's
regels (begint met een letter, alleen [a-z0-9-.], <= 63 tekens).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from span.config import Settings
from span.db.brain import BrainDB
from span.db.schema import init_schema

SHARED_DB = "brain-shared"
_DB_RE = re.compile(r"[^a-z0-9]+")


def user_db_name(oid: str) -> str:
    """Veilige Neo4j-databasenaam voor een gebruiker, afgeleid van de oid.
    Begint altijd met een letter ('brain-'), lowercase, alleen [a-z0-9-]."""
    slug = _DB_RE.sub("-", oid.strip().lower()).strip("-")
    return ("brain-" + slug)[:63] or "brain-unknown"


def user_cache_path(oid: str) -> Path:
    """Pad naar de MSAL-token-cache van één gebruiker (eigen mailbox-tokens)."""
    safe = _DB_RE.sub("-", oid.strip().lower()).strip("-") or "unknown"
    return Path.home() / ".span" / safe / "msal_cache.json"


def user_settings(settings: Settings, db: str) -> Settings:
    """Kopie van de settings met een andere brein-database (frozen dataclass)."""
    return replace(settings, brain_db=db)


def build_user_brain(settings: Settings, db: str) -> BrainDB:
    """Open (en initialiseer indien nodig) een brein-database. Idempotent."""
    s = user_settings(settings, db)
    brain = BrainDB(s)
    brain.verify()
    brain.ensure_database()      # CREATE DATABASE ... IF NOT EXISTS WAIT
    init_schema(brain, s)        # schema + identity, idempotent
    return brain


@dataclass
class UserContext:
    oid: str
    upn: str
    name: str
    brain: BrainDB               # privé-brein van deze gebruiker
    o365: Any = None             # per-user O365-client (eigen token-cache)
    shared: Any = None           # gedeeld brein (read), of None in single-user

    def close(self) -> None:
        try:
            self.brain.close()
        except Exception:
            pass


class ContextRegistry:
    """Lui-opgebouwde, gecachede UserContext per oid. Thread-safe."""

    def __init__(self, settings: Settings,
                 build_o365: Callable[[str], Any] | None = None,
                 brain_factory: Callable[[Settings, str], BrainDB] = build_user_brain,
                 owner_oid: str = ""):
        self._settings = settings
        self._build_o365 = build_o365
        self._brain_factory = brain_factory
        # de 'owner' houdt zijn bestaande brein (settings.brain_db, bv. span-brain)
        # i.p.v. een verse brain-<oid>; zo migreert de huidige NOVA zonder verlies.
        self._owner_oid = owner_oid.strip().lower()
        self._ctx: dict[str, UserContext] = {}
        self._lock = threading.Lock()

    def _db_for(self, oid: str) -> str:
        if self._owner_oid and oid.strip().lower() == self._owner_oid:
            return self._settings.brain_db
        return user_db_name(oid)

    def get(self, oid: str, upn: str = "", name: str = "") -> UserContext:
        with self._lock:
            ctx = self._ctx.get(oid)
            if ctx is not None:
                return ctx
        # buiten de lock bouwen (db-init kan traag zijn); daarna onder lock zetten
        brain = self._brain_factory(self._settings, self._db_for(oid))
        o365 = self._build_o365(oid) if self._build_o365 else None
        try:
            shared = self.shared_brain()
        except Exception:
            shared = None
        ctx = UserContext(oid=oid, upn=upn, name=name, brain=brain, o365=o365,
                          shared=shared)
        with self._lock:
            existing = self._ctx.get(oid)
            if existing is not None:      # race: een ander bouwde 'm net
                ctx.close()
                return existing
            self._ctx[oid] = ctx
            return ctx

    def invalidate(self, oid: str) -> None:
        """Gooi de gecachede context van een gebruiker weg (bv. na (her)login,
        zodat de O365-client de verse token-cache opnieuw inleest)."""
        with self._lock:
            ctx = self._ctx.pop(oid, None)
        if ctx is not None:
            ctx.close()

    def shared_brain(self) -> BrainDB:
        """Het gedeelde brein (brain-shared) — lazy, eenmalig opgebouwd."""
        with self._lock:
            shared = self._ctx.get("__shared__")
        if shared is not None:
            return shared.brain
        brain = self._brain_factory(self._settings, SHARED_DB)
        holder = UserContext(oid="__shared__", upn="", name="shared", brain=brain)
        with self._lock:
            existing = self._ctx.get("__shared__")
            if existing is not None:
                holder.close()
                return existing.brain
            self._ctx["__shared__"] = holder
            return brain

    def close_all(self) -> None:
        with self._lock:
            for ctx in self._ctx.values():
                ctx.close()
            self._ctx.clear()
