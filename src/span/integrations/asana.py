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
        from span.integrations.http import request_with_retry
        resp = request_with_retry(lambda: self._session.get(
            f"{BASE}{path}", params=params, timeout=30))
        resp.raise_for_status()
        return resp.json()["data"]

    def _request(self, method: str, path: str, payload: dict[str, Any],
                 idempotent: bool = True) -> Any:
        from span.integrations.http import request_with_retry
        resp = request_with_retry(lambda: self._session.request(
            method, f"{BASE}{path}", json={"data": payload}, timeout=30),
            idempotent=idempotent)
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
        task = self._request("POST", "/tasks", payload, idempotent=False)
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

    def task_detail(self, task_gid: str) -> dict[str, Any]:
        """Volledige detailweergave van één taak, incl. notities, toegewezene
        en subtaak-teller."""
        t = self._get(
            f"/tasks/{task_gid}",
            {"opt_fields": "name,notes,due_on,assignee.name,projects.name,"
                           "completed,permalink_url,num_subtasks"},
        )
        return {
            "gid": t.get("gid"),
            "name": t.get("name"),
            "notes": (t.get("notes") or "")[:2000],
            "due": t.get("due_on"),
            "completed": t.get("completed"),
            "assignee": (t.get("assignee") or {}).get("name"),
            "projects": [p.get("name") for p in t.get("projects") or []],
            "subtasks": t.get("num_subtasks"),
            "url": t.get("permalink_url"),
        }

    def project_tasks(self, project_gid: str, top: int = 20) -> list[dict[str, Any]]:
        """Onvoltooide taken van een project, in projectvolgorde."""
        tasks = self._get(
            f"/projects/{project_gid}/tasks",
            {
                "completed_since": "now",  # alleen onvoltooide taken
                "limit": min(int(top), 50),
                "opt_fields": TASK_FIELDS,
            },
        )
        return [self._slim(t) for t in tasks if not t.get("completed")]

    def subtasks(self, task_gid: str) -> list[dict[str, Any]]:
        """Subtaken van een taak."""
        items = self._get(f"/tasks/{task_gid}/subtasks",
                          {"opt_fields": TASK_FIELDS})
        return [self._slim(t) for t in items]

    def comments(self, task_gid: str, top: int = 10) -> list[dict[str, Any]]:
        """Comments op een taak (stories van type 'comment'), oudste eerst."""
        stories = self._get(
            f"/tasks/{task_gid}/stories",
            {"opt_fields": "type,text,created_at,created_by.name"},
        )
        out = [
            {"author": (s.get("created_by") or {}).get("name"),
             "text": (s.get("text") or "")[:1000],
             "at": s.get("created_at")}
            for s in stories
            if s.get("type") == "comment"
        ]
        return out[-min(int(top), 50):]

    def sections(self, project_gid: str) -> list[dict[str, Any]]:
        """Secties (kolommen) van een project, voor asana_task_move."""
        items = self._get(f"/projects/{project_gid}/sections",
                          {"opt_fields": "name"})
        return [{"gid": s["gid"], "name": s.get("name")} for s in items]

    def teams(self) -> list[dict[str, Any]]:
        """Teams in de workspace (gid + naam), voor asana_project_create."""
        self._ensure_context()
        items = self._get(f"/workspaces/{self._workspace}/teams",
                          {"opt_fields": "name"})
        return [{"gid": t["gid"], "name": t.get("name")} for t in items]

    def update_task(self, task_gid: str, name: str = "", notes: str = "",
                    due_on: str = "", assignee: str = "") -> dict[str, Any]:
        """Werk alleen de meegegeven velden bij; due_on='geen' haalt de
        deadline weg (stuurt null)."""
        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        if notes:
            payload["notes"] = notes
        if due_on:
            payload["due_on"] = (None if due_on.strip().lower() == "geen"
                                 else due_on)
        if assignee:
            payload["assignee"] = assignee
        if not payload:
            raise ValueError("Niets te wijzigen — geef name/notes/due_on/assignee.")
        task = self._request("PUT", f"/tasks/{task_gid}", payload)
        return {"updated": True, "gid": task_gid, "name": task.get("name"),
                "fields": sorted(payload)}

    def move_task(self, task_gid: str, section_gid: str) -> dict[str, Any]:
        """Verplaats een taak naar een sectie (kolom) binnen z'n project."""
        self._request("POST", f"/sections/{section_gid}/addTask",
                      {"task": task_gid})
        return {"moved": True, "task": task_gid, "section": section_gid}

    def create_project(self, name: str, team_gid: str = "",
                       notes: str = "") -> dict[str, Any]:
        """Maak een nieuw project in de workspace (optioneel binnen een team)."""
        self._ensure_context()
        payload: dict[str, Any] = {"name": name, "workspace": self._workspace}
        if team_gid:
            payload["team"] = team_gid
        if notes:
            payload["notes"] = notes
        project = self._request("POST", "/projects", payload, idempotent=False)
        return {"created": True, "gid": project["gid"],
                "name": project.get("name"), "url": project.get("permalink_url")}

    def add_comment(self, task_gid: str, text: str) -> dict[str, Any]:
        """Plaats een comment op een taak — extern zichtbaar voor het team."""
        story = self._request("POST", f"/tasks/{task_gid}/stories",
                              {"text": text}, idempotent=False)
        return {"commented": True, "gid": story.get("gid"), "task": task_gid}

    def delete_task(self, task_gid: str) -> dict[str, Any]:
        """Verwijder een taak — 30 dagen herstelbaar via de Asana-prullenbak."""
        self._request("DELETE", f"/tasks/{task_gid}", {})
        return {"deleted": True, "gid": task_gid,
                "note": "30 dagen herstelbaar via de Asana-prullenbak."}

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
