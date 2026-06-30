"""Achtergrondtaken met sub-agents — LO werkt door terwijl je blijft praten.

Een taak = een doel dat een verse SpanAgent (sub-agent) op de achtergrond
uitvoert, met hetzelfde brein en dezelfde veiligheidspoort. Schrijfacties van
een sub-agent komen — net als in een gesprek — in de Agent Inbox ter goedkeuring.

De TaskManager houdt de status bij (de HUD pollt /api/tasks). Een kleine
worker-pool begrenst hoeveel taken tegelijk draaien. Elke taak is annuleerbaar
via een threading.Event (de sub-agent checkt het via should_cancel)."""

from __future__ import annotations

import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

ACTIVE = ("queued", "running", "cancelling")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    def __init__(self, runner: Callable[..., str], max_workers: int = 2) -> None:
        # runner(task, set_progress, should_cancel, ctx) -> resultaat-string
        self._runner = runner
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lo-task")
        self._items: dict[int, dict[str, Any]] = {}
        self._cancels: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def submit(self, goal: str, title: str = "", ctx: dict[str, Any] | None = None) -> int:
        tid = next(self._ids)
        item = {"id": tid, "goal": goal, "title": (title or goal[:60]).strip(),
                "status": "queued", "progress": "", "steps": [], "result": "",
                "created": _now(), "updated": _now()}
        with self._lock:
            self._items[tid] = item
            self._cancels[tid] = threading.Event()
            if len(self._items) > 60:  # compact houden
                for k in sorted(self._items)[:-60]:
                    self._items.pop(k, None)
                    self._cancels.pop(k, None)
        self._pool.submit(self._run, tid, ctx or {})
        return tid

    def _update(self, tid: int, **kw: Any) -> None:
        with self._lock:
            it = self._items.get(tid)
            if it:
                it.update(kw)
                it["updated"] = _now()

    def _progress(self, tid: int, label: str) -> None:
        label = (label or "").strip()
        if not label:
            return
        with self._lock:
            it = self._items.get(tid)
            if it:
                it["progress"] = label
                it["steps"].append(label)
                del it["steps"][:-15]
                it["updated"] = _now()

    def _run(self, tid: int, ctx: dict[str, Any]) -> None:
        ev = self._cancels.get(tid) or threading.Event()
        self._update(tid, status="running", progress="gestart")
        try:
            result = self._runner(dict(self._items[tid]),
                                  lambda l: self._progress(tid, l), ev.is_set, ctx)
            if ev.is_set():
                self._update(tid, status="cancelled",
                             result=(result or "(geannuleerd)"), progress="geannuleerd")
            else:
                self._update(tid, status="done", result=(result or ""), progress="klaar")
        except Exception as exc:  # nooit de worker laten crashen
            self._update(tid, status="error",
                         result=f"{type(exc).__name__}: {exc}", progress="fout")

    def cancel(self, tid: int) -> bool:
        ev = self._cancels.get(tid)
        if ev is None:
            return False
        ev.set()
        self._update(tid, status="cancelling")
        return True

    def get(self, tid: int) -> dict[str, Any] | None:
        with self._lock:
            it = self._items.get(tid)
            return dict(it) if it else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in sorted(self._items.values(), key=lambda x: -x["id"])]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for v in self._items.values() if v["status"] in ACTIVE)

    def shutdown(self) -> None:
        for ev in self._cancels.values():
            ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
