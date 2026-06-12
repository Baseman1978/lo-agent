"""Asana via de REST API — personal access token.

Token aanmaken: Asana → instellingen → Apps → Developer console →
Personal access token. Daarna ASANA_TOKEN in .env zetten.
"""

from __future__ import annotations

from typing import Any

import requests

BASE = "https://app.asana.com/api/1.0"
TASK_FIELDS = "name,due_on,completed,notes,permalink_url,projects.name,assignee.name"


class AsanaClient:
    def __init__(self, token: str, workspace_gid: str = ""):
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"
        self._workspace = workspace_gid
        self._me_gid = ""

    # -- helpers ------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._session.get(f"{BASE}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()["data"]

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        resp = self._session.request(method, f"{BASE}{path}", json={"data": payload}, timeout=30)
        resp.raise_for_status()
        return resp.json()["data"]

    def _ensure_context(self) -> None:
        if self._me_gid and self._workspace:
            return
        me = self._get("/users/me", {"opt_fields": "name,workspaces.name"})
        self._me_gid = me["gid"]
        if not self._workspace:
            workspaces = me.get("workspaces") or []
            if not workspaces:
                raise RuntimeError("Geen Asana-workspace gevonden voor dit token.")
            self._workspace = workspaces[0]["gid"]

    def verify(self) -> str:
        self._ensure_context()
        return self._workspace

    # -- taken ----------------------------------------------------------------

    def my_tasks(self, top: int = 20) -> list[dict[str, Any]]:
        """Open taken uit Mijn taken, gesorteerd zoals in Asana."""
        self._ensure_context()
        utl = self._get(
            "/users/me/user_task_list", {"workspace": self._workspace}
        )
        tasks = self._get(
            f"/user_task_lists/{utl['gid']}/tasks",
            {
                "completed_since": "now",  # alleen onvoltooide taken
                "limit": min(int(top), 50),
                "opt_fields": TASK_FIELDS,
            },
        )
        return [self._slim(t) for t in tasks]

    def create_task(
        self,
        name: str,
        notes: str = "",
        due_on: str = "",
        project_gid: str = "",
    ) -> dict[str, Any]:
        self._ensure_context()
        payload: dict[str, Any] = {
            "name": name,
            "assignee": self._me_gid,
            "workspace": self._workspace,
        }
        if notes:
            payload["notes"] = notes
        if due_on:
            payload["due_on"] = due_on  # YYYY-MM-DD
        if project_gid:
            payload["projects"] = [project_gid]
        task = self._request("POST", "/tasks", payload)
        return {"created": True, "gid": task["gid"], "name": task.get("name"),
                "url": task.get("permalink_url")}

    def complete_task(self, task_gid: str) -> dict[str, Any]:
        task = self._request("PUT", f"/tasks/{task_gid}", {"completed": True})
        return {"completed": True, "gid": task_gid, "name": task.get("name")}

    def search_tasks(self, text: str, top: int = 10) -> list[dict[str, Any]]:
        """Typeahead-zoeken (werkt op elk Asana-abonnement)."""
        self._ensure_context()
        hits = self._get(
            f"/workspaces/{self._workspace}/typeahead",
            {
                "resource_type": "task",
                "query": text,
                "count": min(int(top), 25),
                "opt_fields": TASK_FIELDS,
            },
        )
        return [self._slim(t) for t in hits]

    def projects(self, top: int = 30) -> list[dict[str, Any]]:
        self._ensure_context()
        items = self._get(
            f"/workspaces/{self._workspace}/projects",
            {"limit": min(int(top), 50), "opt_fields": "name,archived"},
        )
        return [
            {"gid": p["gid"], "name": p.get("name")}
            for p in items
            if not p.get("archived")
        ]

    @staticmethod
    def _slim(t: dict[str, Any]) -> dict[str, Any]:
        return {
            "gid": t.get("gid"),
            "name": t.get("name"),
            "due": t.get("due_on"),
            "projects": [p.get("name") for p in t.get("projects") or []],
            "url": t.get("permalink_url"),
        }
