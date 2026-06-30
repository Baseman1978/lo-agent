"""Achtergrondtaken met sub-agents — LO werkt door terwijl je blijft praten.

Een taak = een doel dat een verse SpanAgent (sub-agent) op de achtergrond
uitvoert, met hetzelfde brein en dezelfde veiligheidspoort. Schrijfacties van
een sub-agent komen — net als in een gesprek — in de Agent Inbox ter goedkeuring.

Taken worden als Task-nodes in het brein bewaard (net als Cron-nodes), dus de
geschiedenis overleeft een herstart. Een taak die nog liep tijdens een herstart
kan niet hervatten (de thread is weg) en krijgt status 'interrupted'.

Een kleine worker-pool begrenst hoeveel taken tegelijk draaien. Elke taak is
annuleerbaar via een threading.Event (de sub-agent checkt het via should_cancel)."""

from __future__ import annotations

import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

ACTIVE = ("queued", "running", "cancelling")
_DONE = ("done", "error", "cancelled", "interrupted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    def __init__(self, runner: Callable[..., str], brain: Any = None,
                 max_workers: int = 2, team_runner: Callable[..., str] | None = None) -> None:
        # runner(task, set_progress, should_cancel, ctx) -> resultaat-string
        self._runner = runner
        self._team_runner = team_runner  # coördinator + parallelle sub-agents
        self._brain = brain
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lo-task")
        self._items: dict[int, dict[str, Any]] = {}
        self._cancels: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        if brain is not None:
            self._load()

    # -- persistentie (best effort; mag de runner nooit breken) -------------
    def _persist(self, item: dict[str, Any]) -> None:
        if self._brain is None:
            return
        try:
            self._brain.run(
                """
                MERGE (t:Task {id: $id})
                ON CREATE SET t.created = $created
                SET t.goal=$goal, t.title=$title, t.status=$status,
                    t.progress=$progress, t.result=$result, t.updated=$updated,
                    t.owner=$owner, t.team=$team
                """,
                id=item["id"], created=item["created"], goal=item["goal"][:2000],
                title=item["title"], status=item["status"], progress=item["progress"],
                result=(item["result"] or "")[:6000], updated=item["updated"],
                owner=item.get("owner", ""), team=bool(item.get("team")),
            )
        except Exception:
            pass

    def _load(self) -> None:
        """Bij opstart: bewaarde taken inladen; nog-lopende -> 'interrupted'.
        Houdt alleen de recentste ~60 (rest opruimen)."""
        try:
            rows = self._brain.run(
                "MATCH (t:Task) RETURN t.id AS id, t.goal AS goal, t.title AS title, "
                "t.status AS status, t.progress AS progress, t.result AS result, "
                "t.owner AS owner, t.team AS team, "
                "t.created AS created, t.updated AS updated ORDER BY t.id DESC LIMIT 60"
            )
        except Exception:
            rows = []
        maxid = 0
        for r in rows:
            tid = int(r["id"])
            maxid = max(maxid, tid)
            status = r["status"]
            if status in ACTIVE:  # liep nog bij de herstart -> kan niet hervatten
                status = "interrupted"
            item = {"id": tid, "goal": r["goal"] or "", "title": r["title"] or "",
                    "status": status, "progress": r["progress"] or "",
                    "percent": 100 if status == "done" else 0,
                    "steps": [], "result": r["result"] or "",
                    "owner": r.get("owner") or "", "team": bool(r.get("team")),
                    "created": r["created"] or _now(), "updated": r["updated"] or _now()}
            self._items[tid] = item
            self._cancels[tid] = threading.Event()
            if status == "interrupted":
                self._persist(item)
        # ruim eventueel oudere nodes op
        try:
            self._brain.run(
                "MATCH (t:Task) WITH t ORDER BY t.id DESC SKIP 60 DETACH DELETE t")
        except Exception:
            pass
        self._ids = itertools.count(maxid + 1)

    # -- API ----------------------------------------------------------------
    def submit(self, goal: str, title: str = "", ctx: dict[str, Any] | None = None,
               team: bool = False, owner: str = "") -> int:
        tid = next(self._ids)
        item = {"id": tid, "goal": goal, "title": (title or goal[:60]).strip(),
                "status": "queued", "progress": "", "percent": 0, "steps": [],
                "result": "", "team": bool(team), "owner": owner,
                "created": _now(), "updated": _now()}
        with self._lock:
            self._items[tid] = item
            self._cancels[tid] = threading.Event()
            if len(self._items) > 80:  # compact houden
                for k in sorted(self._items)[:-80]:
                    self._items.pop(k, None)
                    self._cancels.pop(k, None)
        self._persist(item)
        self._pool.submit(self._run, tid, ctx or {})
        return tid

    def _update(self, tid: int, persist: bool = False, **kw: Any) -> None:
        with self._lock:
            it = self._items.get(tid)
            if not it:
                return
            it.update(kw)
            it["updated"] = _now()
            snap = dict(it) if persist else None
        if snap is not None:
            self._persist(snap)

    def _progress(self, tid: int, label: str = "", percent: int | None = None) -> None:
        label = (label or "").strip()
        with self._lock:
            it = self._items.get(tid)
            if not it:
                return
            if label:
                it["progress"] = label
                it["steps"].append(label)
                del it["steps"][:-15]
            if percent is not None:
                it["percent"] = max(0, min(100, int(percent)))
            it["updated"] = _now()

    def _run(self, tid: int, ctx: dict[str, Any]) -> None:
        ev = self._cancels.get(tid) or threading.Event()
        self._update(tid, persist=True, status="running", progress="gestart", percent=3)
        runner = self._runner
        if self._items.get(tid, {}).get("team") and self._team_runner is not None:
            runner = self._team_runner
        try:
            result = runner(
                dict(self._items[tid]),
                lambda label="", percent=None: self._progress(tid, label, percent),
                ev.is_set, ctx)
            if ev.is_set():
                self._update(tid, persist=True, status="cancelled",
                             result=(result or "(geannuleerd)"), progress="geannuleerd")
            else:
                self._update(tid, persist=True, status="done",
                             result=(result or ""), progress="klaar", percent=100)
        except Exception as exc:  # nooit de worker laten crashen
            self._update(tid, persist=True, status="error",
                         result=f"{type(exc).__name__}: {exc}", progress="fout")

    def cancel(self, tid: int) -> bool:
        ev = self._cancels.get(tid)
        if ev is None:
            return False
        ev.set()
        self._update(tid, persist=True, status="cancelling")
        return True

    def get(self, tid: int) -> dict[str, Any] | None:
        with self._lock:
            it = self._items.get(tid)
            return dict(it) if it else None

    def _visible(self, v: dict[str, Any], owner: str | None) -> bool:
        # owner=None -> alles (intern); anders strikt alleen eigen taken. Lege owner
        # (legacy) is dus NIET 'van iedereen' -> geen footgun bij een lege brein-db.
        return owner is None or (v.get("owner") or "") == owner

    def list(self, owner: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            vals = sorted(self._items.values(), key=lambda x: -x["id"])
        return [dict(v) for v in vals if self._visible(v, owner)]

    def active_count(self, owner: str | None = None) -> int:
        with self._lock:
            return sum(1 for v in self._items.values()
                       if v["status"] in ACTIVE and self._visible(v, owner))

    def shutdown(self) -> None:
        for ev in self._cancels.values():
            ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
