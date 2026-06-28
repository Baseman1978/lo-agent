"""Per-user context-fundament: db-naam-afleiding + registry-caching."""

from span.server.usercontext import ContextRegistry, user_cache_path, user_db_name


class _FakeBrain:
    def __init__(self, db):
        self.database = db
        self.closed = False

    def close(self):
        self.closed = True


def test_user_db_name_safe():
    oid = "ABC123-De4f-5678-90ab-CDEF12345678"
    db = user_db_name(oid)
    assert db.startswith("brain-")
    assert db == db.lower()
    assert all(c.isalnum() or c == "-" for c in db)
    assert len(db) <= 63
    # leeg/rommelig blijft geldig
    assert user_db_name("").startswith("brain-")
    assert user_db_name("!!!").startswith("brain-")


def test_registry_caches_per_oid():
    calls = []

    def fake_factory(settings, db):
        calls.append(db)
        return _FakeBrain(db)

    reg = ContextRegistry(settings=None, build_o365=lambda oid: f"o365:{oid}",
                          brain_factory=fake_factory)
    a1 = reg.get("user-a", upn="a@lomans.nl", name="A")
    a2 = reg.get("user-a")
    b1 = reg.get("user-b")

    assert a1 is a2                      # zelfde oid -> gecached
    assert a1 is not b1                  # andere oid -> eigen context
    assert a1.o365 == "o365:user-a"
    assert a1.brain.database == "brain-user-a"
    assert b1.brain.database == "brain-user-b"
    assert a1.shared.database == "brain-shared"        # gedeeld brein gekoppeld
    # factory 1x per oid + 1x voor het gedeelde brein (gecached)
    assert calls == ["brain-user-a", "brain-shared", "brain-user-b"]


class _Settings:
    brain_db = "span-brain"


def test_owner_keeps_existing_brain():
    seen = []

    def fake_factory(settings, db):
        seen.append(db)
        return _FakeBrain(db)

    reg = ContextRegistry(settings=_Settings(), brain_factory=fake_factory,
                          owner_oid="OWNER-OID")
    owner = reg.get("owner-oid")          # case-insensitief op de owner
    other = reg.get("someone-else")
    assert owner.brain.database == "span-brain"        # owner houdt z'n brein
    assert other.brain.database == "brain-someone-else"
    assert seen == ["span-brain", "brain-shared", "brain-someone-else"]


def test_shared_brain_singleton():
    def fake_factory(settings, db):
        return _FakeBrain(db)

    reg = ContextRegistry(settings=None, brain_factory=fake_factory)
    s1 = reg.shared_brain()
    s2 = reg.shared_brain()
    assert s1 is s2
    assert s1.database == "brain-shared"


def test_user_cache_path_safe():
    p = user_cache_path("ABC-123!@#/x")
    assert p.name == "msal_cache.json"
    assert ".span" in str(p)
    assert all(c.isalnum() or c == "-" for c in p.parent.name)   # oid-map gesanitized


def test_registry_invalidate_rebuilds():
    def fake_factory(settings, db):
        return _FakeBrain(db)

    reg = ContextRegistry(settings=None, brain_factory=fake_factory)
    a = reg.get("user-x")
    reg.invalidate("user-x")
    assert a.brain.closed is True       # oude context netjes gesloten
    b = reg.get("user-x")
    assert b is not a                   # opnieuw opgebouwd
