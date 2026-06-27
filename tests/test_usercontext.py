"""Per-user context-fundament: db-naam-afleiding + registry-caching."""

from span.server.usercontext import ContextRegistry, user_db_name


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
    assert calls == ["brain-user-a", "brain-user-b"]   # factory 1x per oid


def test_shared_brain_singleton():
    def fake_factory(settings, db):
        return _FakeBrain(db)

    reg = ContextRegistry(settings=None, brain_factory=fake_factory)
    s1 = reg.shared_brain()
    s2 = reg.shared_brain()
    assert s1 is s2
    assert s1.database == "brain-shared"
