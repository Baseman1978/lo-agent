"""Office 365 via Microsoft Graph — delegated permissions, device code flow.

Eenmalig inloggen via `span o365-login` (of de knop in de web-UI); daarna
houdt de MSAL token-cache (refresh token) de sessie levend. Alle calls
lopen onder het account van de gebruiker, met precies de scopes hieronder.
"""

from __future__ import annotations

import atexit
import json
from pathlib import Path
from typing import Any

import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Tasks.ReadWrite",
    # uitgebreid: hele O365-suite via hetzelfde app-login-token (delegated)
    "Files.Read.All",     # OneDrive + gedeelde bestanden zoeken/lezen
    "Sites.Read.All",     # SharePoint-sites doorzoeken
    "Chat.Read",          # Teams-chatberichten doorzoeken
    "People.Read",        # personen/collega's opzoeken
]
TIMEZONE = "W. Europe Standard Time"


def odata_quote(value: str) -> str:
    """OData string-literal: enkele quotes verdubbelen en omsluiten (M10).
    Centrale helper voor álle $filter-stringwaarden tegen filter-injectie."""
    return "'" + str(value).replace("'", "''") + "'"


def _search_hits(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Vlak de hitsContainers van de Microsoft Search-API (/search/query) uit."""
    out: list[dict[str, Any]] = []
    for resp in data.get("value", []):
        for hc in resp.get("hitsContainers", []):
            for hit in hc.get("hits", []):
                res = hit.get("resource", {}) or {}
                fields = res.get("fields") or {}
                out.append({
                    "summary": (hit.get("summary") or "")[:300],
                    "name": res.get("name") or res.get("subject") or fields.get("title"),
                    "link": res.get("webUrl") or res.get("webLink") or fields.get("webUrl"),
                    "from": ((res.get("from") or {}).get("emailAddress") or {}).get("name"),
                    "type": str(res.get("@odata.type", "")).split(".")[-1],
                })
    return out


class NotAuthenticated(RuntimeError):
    """Nog geen (geldige) login — start de device code flow."""


class O365Client:
    def __init__(self, client_id: str, tenant_id: str, cache_path: Path,
                 client_secret: str = ""):
        self._cache_path = cache_path
        self._cache = msal.SerializableTokenCache()
        if cache_path.exists():
            self._cache.deserialize(cache_path.read_text(encoding="utf-8"))
        atexit.register(self._persist_cache)
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        # Met een secret: confidential web-app (browser OIDC auth-code login).
        # Zonder secret: public client (device-code flow). De refresh-tokens van
        # een confidential client kunnen alleen door een confidential client
        # vernieuwd worden, dus login én connector delen dezelfde app + cache.
        if client_secret:
            self._app: msal.ClientApplication = msal.ConfidentialClientApplication(
                client_id, authority=authority,
                client_credential=client_secret, token_cache=self._cache,
            )
        else:
            self._app = msal.PublicClientApplication(
                client_id, authority=authority, token_cache=self._cache,
            )

    # -- browser-login (OIDC auth-code + PKCE, via msal) ------------------

    def build_auth_flow(self, redirect_uri: str) -> dict[str, Any]:
        """Start de browser-login: msal genereert state, nonce en PKCE-verifier.
        Bewaar de teruggegeven flow tot de callback; stuur de gebruiker naar
        flow['auth_uri']."""
        return self._app.initiate_auth_code_flow(
            scopes=SCOPES, redirect_uri=redirect_uri)

    def redeem_auth_flow(self, flow: dict[str, Any],
                         auth_response: dict[str, Any]) -> dict[str, Any]:
        """Rond de callback af: wissel de code in voor tokens (vult de cache die
        de connectors gebruiken) en geef de id_token-claims terug (identiteit)."""
        result = self._app.acquire_token_by_auth_code_flow(flow, auth_response)
        if "access_token" not in result:
            raise RuntimeError(result.get(
                "error_description", result.get("error", "Login mislukt.")))
        self._persist_cache()
        return result.get("id_token_claims", {}) or {}

    def cache_dump(self) -> str:
        """Serialiseer de huidige token-cache (voor het wegschrijven naar de
        per-user cache na een browser-login)."""
        return self._cache.serialize()

    def _persist_cache(self) -> None:
        if self._cache.has_state_changed:
            # refresh tokens met Mail.Send-rechten: alleen eigenaar mag lezen
            self._cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._cache_path.write_text(self._cache.serialize(), encoding="utf-8")
            try:
                self._cache_path.chmod(0o600)
            except OSError:
                pass  # Windows kent geen POSIX-modes; daar beschermt het profiel

    # -- auth -------------------------------------------------------------

    def is_authenticated(self) -> bool:
        try:
            self._token()
            return True
        except NotAuthenticated:
            return False

    def account_name(self) -> str | None:
        accounts = self._app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def start_device_flow(self) -> dict[str, Any]:
        """Stap 1: geef de code + URL terug die de gebruiker moet bezoeken."""
        flow = self._app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow start mislukt: {json.dumps(flow)}")
        return flow

    def complete_device_flow(self, flow: dict[str, Any]) -> str:
        """Stap 2 (blokkeert tot login of timeout): rond de flow af."""
        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(result.get("error_description", "Login mislukt."))
        self._persist_cache()
        return self.account_name() or "onbekend account"

    def logout(self) -> str | None:
        """Ontkoppel het account: cache leegmaken + cache-bestand weg."""
        name = self.account_name()
        for acct in self._app.get_accounts():
            self._app.remove_account(acct)
        self._persist_cache()
        if self._cache_path.exists():
            self._cache_path.unlink()
        return name

    def _token(self) -> str:
        accounts = self._app.get_accounts()
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._persist_cache()
                return result["access_token"]
        raise NotAuthenticated(
            "Niet ingelogd bij Microsoft 365. Draai `span o365-login` of gebruik "
            "de login-knop in de web-UI."
        )

    # -- graph helpers ----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Prefer": f'outlook.timezone="{TIMEZONE}"',
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        from span.integrations.http import request_with_retry
        resp = request_with_retry(lambda: requests.get(
            f"{GRAPH}{path}", headers=self._headers(), params=params, timeout=30))
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        from span.integrations.http import request_with_retry
        resp = request_with_retry(lambda: requests.post(
            f"{GRAPH}{path}", headers=self._headers(), json=payload, timeout=30))
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- mail ---------------------------------------------------------------

    def inbox(self, top: int = 10, unread_only: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "$top": min(int(top), 25),
            "$orderby": "receivedDateTime desc",
            "$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview,isRead,webLink",
        }
        if unread_only:
            params["$filter"] = "isRead eq false"
        data = self._get("/me/mailFolders/inbox/messages", params)
        return [
            {
                "graph_id": m.get("id"),
                "conversation_id": m.get("conversationId"),
                "subject": m.get("subject"),
                "from": (m.get("from") or {}).get("emailAddress", {}).get("name"),
                "received": m.get("receivedDateTime"),
                "preview": (m.get("bodyPreview") or "")[:200],
                "unread": not m.get("isRead", True),
                "link": m.get("webLink"),
            }
            for m in data.get("value", [])
        ]

    def draft_reply(self, message_id: str, body: str) -> dict[str, Any]:
        """Maak een antwoord-CONCEPT in Outlook Drafts — verstuurt niets."""
        draft = self._post(f"/me/messages/{message_id}/createReply", {})
        draft_id = draft.get("id")
        resp = requests.patch(
            f"{GRAPH}/me/messages/{draft_id}",
            headers=self._headers(),
            json={"body": {"contentType": "Text", "content": body}},
            timeout=30,
        )
        resp.raise_for_status()
        return {"draft_created": True, "draft_id": draft_id,
                "link": resp.json().get("webLink")}

    def conversation_messages(self, conversation_id: str, top: int = 15) -> list[dict[str, Any]]:
        """Alle berichten in een mailthread (voor samenvatting)."""
        data = self._get(
            "/me/messages",
            {
                # M10: alle $filter-stringwaarden via één escape-helper
                "$filter": f"conversationId eq {odata_quote(conversation_id)}",
                "$top": min(int(top), 25),
                "$select": "subject,from,receivedDateTime,bodyPreview",
                "$orderby": "receivedDateTime",
            },
        )
        return [
            {
                "from": (m.get("from") or {}).get("emailAddress", {}).get("name"),
                "received": m.get("receivedDateTime"),
                "preview": (m.get("bodyPreview") or "")[:400],
            }
            for m in data.get("value", [])
        ]

    def send_mail(self, to: list[str], subject: str, body: str) -> dict[str, Any]:
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
            },
            "saveToSentItems": True,
        }
        self._post("/me/sendMail", payload)
        return {"sent": True, "to": to, "subject": subject}

    # -- agenda ---------------------------------------------------------------

    def calendar(self, days: int = 1) -> list[dict[str, Any]]:
        """Afspraken van nu tot +days, in lokale tijd (W. Europe)."""
        from datetime import datetime, timedelta, timezone

        start = datetime.now(timezone.utc)
        end = start + timedelta(days=max(1, min(int(days), 31)))
        data = self._get(
            "/me/calendarView",
            {
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$orderby": "start/dateTime",
                "$top": 50,
                "$select": "subject,start,end,location,organizer,isAllDay,onlineMeeting",
            },
        )
        return [
            {
                "subject": e.get("subject"),
                "start": (e.get("start") or {}).get("dateTime"),
                "end": (e.get("end") or {}).get("dateTime"),
                "location": (e.get("location") or {}).get("displayName"),
                "organizer": (e.get("organizer") or {}).get("emailAddress", {}).get("name"),
                "all_day": e.get("isAllDay", False),
                "join_url": (e.get("onlineMeeting") or {}).get("joinUrl"),
            }
            for e in data.get("value", [])
        ]

    def create_event(
        self,
        subject: str,
        start_iso: str,
        end_iso: str,
        attendees: list[str] | None = None,
        body: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject": subject,
            "start": {"dateTime": start_iso, "timeZone": TIMEZONE},
            "end": {"dateTime": end_iso, "timeZone": TIMEZONE},
        }
        if body:
            payload["body"] = {"contentType": "Text", "content": body}
        if attendees:
            payload["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"} for a in attendees
            ]
        created = self._post("/me/events", payload)
        return {"created": True, "id": created.get("id"), "subject": subject, "start": start_iso}

    def unanswered_sent(self, days: int = 5, top: int = 15) -> list[dict[str, Any]]:
        """Verzonden mails met een vraag waarop nog geen antwoord kwam."""
        from datetime import datetime, timedelta, timezone

        me = (self._get("/me", {"$select": "displayName"})).get("displayName", "")
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sent = self._get(
            "/me/mailFolders/sentitems/messages",
            {
                "$top": min(int(top), 25),
                "$orderby": "sentDateTime desc",
                "$filter": f"sentDateTime ge {since}",
                "$select": "subject,toRecipients,sentDateTime,conversationId,bodyPreview",
            },
        ).get("value", [])

        pending: list[dict[str, Any]] = []
        seen_conversations: set[str] = set()
        for m in sent:
            conv = m.get("conversationId") or ""
            preview = m.get("bodyPreview") or ""
            if not conv or conv in seen_conversations or "?" not in (preview + (m.get("subject") or "")):
                continue
            seen_conversations.add(conv)
            thread = self.conversation_messages(conv, top=10)
            if thread and thread[-1].get("from") == me:  # laatste woord is aan mij → wacht op ander
                to = [r.get("emailAddress", {}).get("name") for r in m.get("toRecipients", [])]
                pending.append({
                    "subject": m.get("subject"),
                    "to": [t for t in to if t],
                    "sent": m.get("sentDateTime"),
                    "conversation_id": conv,
                })
            if len(pending) >= 5:
                break
        return pending

    # -- Microsoft To Do -------------------------------------------------------

    def _default_todo_list(self) -> str | None:
        lists = self._get("/me/todo/lists").get("value", [])
        default = next(
            (l for l in lists if l.get("wellknownListName") == "defaultList"),
            lists[0] if lists else None,
        )
        return default["id"] if default else None

    def todo_tasks(self, top: int = 20) -> list[dict[str, Any]]:
        """Open taken uit de standaard takenlijst."""
        list_id = self._default_todo_list()
        if list_id is None:
            return []
        data = self._get(
            f"/me/todo/lists/{list_id}/tasks",
            {"$top": min(int(top), 50), "$filter": "status ne 'completed'"},
        )
        return [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "due": ((t.get("dueDateTime") or {}).get("dateTime") or "")[:10] or None,
                "importance": t.get("importance"),
            }
            for t in data.get("value", [])
        ]

    def todo_create(self, title: str, due: str = "", body: str = "") -> dict[str, Any]:
        """Nieuwe taak in de standaard To Do-lijst. due = YYYY-MM-DD."""
        list_id = self._default_todo_list()
        if list_id is None:
            raise RuntimeError("Geen To Do-lijst gevonden.")
        payload: dict[str, Any] = {"title": title}
        if due:
            payload["dueDateTime"] = {"dateTime": f"{due}T09:00:00", "timeZone": TIMEZONE}
        if body:
            payload["body"] = {"content": body, "contentType": "text"}
        task = self._post(f"/me/todo/lists/{list_id}/tasks", payload)
        return {"created": True, "id": task.get("id"), "title": title, "due": due or None}

    def todo_complete(self, task_id: str) -> dict[str, Any]:
        """Vink een To Do-taak af."""
        list_id = self._default_todo_list()
        resp = requests.patch(
            f"{GRAPH}/me/todo/lists/{list_id}/tasks/{task_id}",
            headers=self._headers(),
            json={"status": "completed"},
            timeout=30,
        )
        resp.raise_for_status()
        return {"completed": True, "id": task_id}

    # -- mail-zoeken over ALLE mappen ----------------------------------------

    def search_mail(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        """Zoek over de HELE mailbox (alle mappen incl. Archief/Verwijderd),
        niet alleen de inbox. Graph $search = KQL-relevantie."""
        data = self._get("/me/messages", {
            "$search": f'"{query}"',
            "$top": min(int(top), 25),
            "$select": "id,conversationId,subject,from,receivedDateTime,"
                       "bodyPreview,isRead,webLink,parentFolderId",
        })
        return [{
            "graph_id": m.get("id"),
            "conversation_id": m.get("conversationId"),
            "subject": m.get("subject"),
            "from": (m.get("from") or {}).get("emailAddress", {}).get("name"),
            "received": m.get("receivedDateTime"),
            "preview": (m.get("bodyPreview") or "")[:200],
            "unread": not m.get("isRead", True),
            "link": m.get("webLink"),
            "folder_id": m.get("parentFolderId"),
        } for m in data.get("value", [])]

    def list_folders(self, top: int = 60) -> list[dict[str, Any]]:
        """Mailmappen met aantallen (Inbox, Archief, Verzonden, eigen mappen…)."""
        data = self._get("/me/mailFolders", {
            "$top": min(int(top), 100),
            "$select": "id,displayName,totalItemCount,unreadItemCount",
        })
        return [{
            "id": f.get("id"), "name": f.get("displayName"),
            "total": f.get("totalItemCount"), "unread": f.get("unreadItemCount"),
        } for f in data.get("value", [])]

    # -- agenda-zoeken --------------------------------------------------------

    def calendar_search(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        """Zoek afspraken op trefwoord (titel/locatie/organisator), alle datums."""
        data = self._get("/me/events", {
            "$search": f'"{query}"',
            "$top": min(int(top), 25),
            "$select": "subject,start,end,location,organizer,webLink",
        })
        return [{
            "subject": e.get("subject"),
            "start": (e.get("start") or {}).get("dateTime"),
            "end": (e.get("end") or {}).get("dateTime"),
            "location": (e.get("location") or {}).get("displayName"),
            "organizer": (e.get("organizer") or {}).get("emailAddress", {}).get("name"),
            "link": e.get("webLink"),
        } for e in data.get("value", [])]

    # -- bestanden (OneDrive) -------------------------------------------------

    def search_files(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        """Zoek bestanden in OneDrive en gedeelde items."""
        q = str(query).replace("'", "''")
        data = self._get(f"/me/drive/root/search(q='{q}')", {
            "$top": min(int(top), 25),
            "$select": "id,name,webUrl,size,lastModifiedDateTime,file,folder",
        })
        return [{
            "id": it.get("id"), "name": it.get("name"), "link": it.get("webUrl"),
            "size": it.get("size"), "modified": it.get("lastModifiedDateTime"),
            "is_folder": "folder" in it,
        } for it in data.get("value", [])]

    def read_file(self, item_id: str, max_chars: int = 4000) -> dict[str, Any]:
        """Metadata + (voor tekstbestanden) de inhoud van een OneDrive-bestand."""
        meta = self._get(f"/me/drive/items/{item_id}",
                         {"$select": "id,name,webUrl,size,file"})
        name = meta.get("name") or ""
        mime = (meta.get("file") or {}).get("mimeType", "")
        out: dict[str, Any] = {"id": meta.get("id"), "name": name,
                               "link": meta.get("webUrl"), "mime": mime}
        if mime.startswith("text/") or name.endswith((".txt", ".md", ".csv", ".json", ".log")):
            from span.integrations.http import request_with_retry
            r = request_with_retry(lambda: requests.get(
                f"{GRAPH}/me/drive/items/{item_id}/content",
                headers={"Authorization": f"Bearer {self._token()}"}, timeout=30))
            if r.ok:
                out["content"] = r.text[:max_chars]
        else:
            out["note"] = "Office/binair bestand — open via de link of voeg toe aan het geheugen."
        return out

    # -- SharePoint -----------------------------------------------------------

    def search_sharepoint(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        """Doorzoek SharePoint (documenten + lijstitems) via de Search-API."""
        data = self._post("/search/query", {"requests": [{
            "entityTypes": ["driveItem", "listItem"],
            "query": {"queryString": query},
            "from": 0, "size": min(int(top), 25),
        }]})
        return _search_hits(data)

    # -- Teams ----------------------------------------------------------------

    def search_chat(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        """Doorzoek Teams-chatberichten via de Search-API."""
        data = self._post("/search/query", {"requests": [{
            "entityTypes": ["chatMessage"],
            "query": {"queryString": query},
            "from": 0, "size": min(int(top), 25),
        }]})
        return _search_hits(data)

    # -- personen -------------------------------------------------------------

    def search_people(self, query: str, top: int = 10) -> list[dict[str, Any]]:
        """Zoek personen/collega's (naam, e-mail, functie, afdeling)."""
        data = self._get("/me/people", {
            "$search": f'"{query}"',
            "$top": min(int(top), 15),
            "$select": "displayName,scoredEmailAddresses,jobTitle,department",
        })
        return [{
            "name": p.get("displayName"),
            "email": ((p.get("scoredEmailAddresses") or [{}])[0] or {}).get("address"),
            "title": p.get("jobTitle"), "department": p.get("department"),
        } for p in data.get("value", [])]

    # -- mail-bijlagen --------------------------------------------------------

    def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        """Lijst de bijlagen van een mail (id, naam, type, grootte)."""
        data = self._get(f"/me/messages/{message_id}/attachments",
                         {"$select": "id,name,contentType,size"})
        return [{
            "id": a.get("id"), "name": a.get("name"),
            "type": a.get("contentType"), "size": a.get("size"),
        } for a in data.get("value", [])]

    def download_attachment(self, message_id: str, attachment_id: str) -> tuple[str, bytes]:
        """Download één bijlage; geeft (bestandsnaam, ruwe bytes) terug."""
        import base64
        a = self._get(f"/me/messages/{message_id}/attachments/{attachment_id}")
        name = a.get("name") or "bijlage"
        b64 = a.get("contentBytes")
        if not b64:
            raise ValueError("Geen bestandsbijlage (mogelijk een inline- of item-bijlage).")
        return name, base64.b64decode(b64)
