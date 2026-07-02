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
    "Files.Read.All",      # OneDrive + gedeelde bestanden zoeken/lezen
    "Files.ReadWrite.All", # bestanden schrijven/maken + Excel-cellen bewerken (Fase 2)
    "Sites.Read.All",      # SharePoint-sites doorzoeken
    "Chat.Read",           # Teams-chatberichten doorzoeken
    "People.Read",         # personen/collega's opzoeken
]
TIMEZONE = "W. Europe Standard Time"

# Power BI is een APARTE resource (eigen audience) — een eigen token via hetzelfde
# ingelogde account. De scopes (Report/Dashboard/Dataset/Workspace.Read.All) zijn
# tenant-breed geconsent; .default vraagt precies die toegekende scopes.
POWERBI = "https://api.powerbi.com/v1.0/myorg"
POWERBI_SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]


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
                    # voor downloaden van SharePoint-bestanden (o365_file_read met drive_id)
                    "drive_id": (res.get("parentReference") or {}).get("driveId"),
                    "item_id": res.get("id"),
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

    def _token_for(self, scopes: list[str]) -> str:
        accounts = self._app.get_accounts()
        if accounts:
            result = self._app.acquire_token_silent(scopes, account=accounts[0])
            if result and "access_token" in result:
                self._persist_cache()
                return result["access_token"]
        raise NotAuthenticated(
            "Niet ingelogd bij Microsoft 365. Draai `span o365-login` of gebruik "
            "de login-knop in de web-UI."
        )

    def _token(self) -> str:
        return self._token_for(SCOPES)

    # -- Power BI (aparte resource, alleen-lezen) ---------------------------
    def powerbi_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        from span.integrations.http import request_with_retry
        tok = self._token_for(POWERBI_SCOPES)
        resp = request_with_retry(lambda: requests.get(
            f"{POWERBI}/{path.lstrip('/')}", params=params,
            headers={"Authorization": f"Bearer {tok}"}, timeout=30))
        resp.raise_for_status()
        return resp.json()

    def powerbi_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST op de Power BI-API. Voor executeQueries (DAX) — dat is een
        alleen-lezen query, wijzigt niets aan de dataset."""
        from span.integrations.http import request_with_retry
        tok = self._token_for(POWERBI_SCOPES)
        resp = request_with_retry(lambda: requests.post(
            f"{POWERBI}/{path.lstrip('/')}", json=payload,
            headers={"Authorization": f"Bearer {tok}"}, timeout=60))
        resp.raise_for_status()
        return resp.json() if resp.content else {}

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

    def _post(self, path: str, payload: dict[str, Any],
              idempotent: bool = True) -> dict[str, Any]:
        # idempotent=False voor externe/onomkeerbare sends (mail/afspraak) -> geen
        # blinde retry bij timeout (voorkomt dubbele verzending, audit H2).
        from span.integrations.http import request_with_retry
        resp = request_with_retry(lambda: requests.post(
            f"{GRAPH}{path}", headers=self._headers(), json=payload, timeout=30),
            idempotent=idempotent)
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
        self._post("/me/sendMail", payload, idempotent=False)
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
        created = self._post("/me/events", payload, idempotent=False)
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
            (li for li in lists if li.get("wellknownListName") == "defaultList"),
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

    def folder_messages(self, folder_id: str, top: int = 50, skip: int = 0,
                        since_days: int = 0) -> list[dict[str, Any]]:
        """Berichten uit één map, nieuwste eerst, optioneel alleen de laatste
        N dagen (receivedDateTime-filter) — voor systematisch archiveren."""
        import datetime as _dt
        params: dict[str, Any] = {
            "$top": min(int(top), 50), "$skip": max(int(skip), 0),
            "$orderby": "receivedDateTime desc",
            "$select": "id,conversationId,subject,from,receivedDateTime,"
                       "bodyPreview,hasAttachments,webLink",
        }
        if since_days and int(since_days) > 0:
            since = (_dt.datetime.utcnow() - _dt.timedelta(days=int(since_days)))
            params["$filter"] = f"receivedDateTime ge {since.strftime('%Y-%m-%dT00:00:00Z')}"
        data = self._get(f"/me/mailFolders/{folder_id}/messages", params)
        return [{
            "graph_id": m.get("id"),
            "conversation_id": m.get("conversationId"),
            "subject": m.get("subject"),
            "from": (m.get("from") or {}).get("emailAddress", {}).get("name"),
            "received": m.get("receivedDateTime"),
            "preview": (m.get("bodyPreview") or "")[:300],
            "has_attachments": m.get("hasAttachments", False),
            "link": m.get("webLink"),
        } for m in data.get("value", [])]

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

    # -- bestanden downloaden + Excel-data -----------------------------------

    def download_file(self, item_id: str) -> tuple[str, bytes]:
        """Download een OneDrive/SharePoint-bestand: (bestandsnaam, ruwe bytes)."""
        from span.integrations.http import request_with_retry
        meta = self._get(f"/me/drive/items/{item_id}", {"$select": "id,name"})
        name = meta.get("name") or "bestand"
        r = request_with_retry(lambda: requests.get(
            f"{GRAPH}/me/drive/items/{item_id}/content",
            headers={"Authorization": f"Bearer {self._token()}"}, timeout=60))
        r.raise_for_status()
        return name, r.content

    def download_drive_item(self, drive_id: str, item_id: str) -> tuple[str, bytes]:
        """Download een bestand uit een willekeurige (SharePoint-)drive: (naam, bytes).
        Gebruik drive_id + item_id uit o365_sharepoint_search."""
        from span.integrations.http import request_with_retry
        meta = self._get(f"/drives/{drive_id}/items/{item_id}", {"$select": "id,name"})
        name = meta.get("name") or "bestand"
        r = request_with_retry(lambda: requests.get(
            f"{GRAPH}/drives/{drive_id}/items/{item_id}/content",
            headers={"Authorization": f"Bearer {self._token()}"}, timeout=120))
        r.raise_for_status()
        return name, r.content

    def excel_worksheets(self, item_id: str) -> list[dict[str, Any]]:
        """Werkbladen van een Excel-bestand (naam, positie, zichtbaar)."""
        data = self._get(f"/me/drive/items/{item_id}/workbook/worksheets",
                         {"$select": "name,position,visibility"})
        return [{"name": w.get("name"), "position": w.get("position"),
                 "visible": w.get("visibility") == "Visible"} for w in data.get("value", [])]

    def excel_read(self, item_id: str, worksheet: str | None = None,
                  address: str | None = None, max_rows: int = 60) -> dict[str, Any]:
        """Lees de gebruikte cellen (of een specifiek bereik zoals 'A1:D20') van
        een Excel-werkblad als rijen. Default = eerste werkblad, hele usedRange."""
        from urllib.parse import quote
        if not worksheet:
            ws = self.excel_worksheets(item_id)
            worksheet = ws[0]["name"] if ws else "Blad1"
        base = f"/me/drive/items/{item_id}/workbook/worksheets/{quote(worksheet)}"
        if address:
            path = f"{base}/range(address='{address}')"
        else:
            path = f"{base}/usedRange"
        data = self._get(path, {"$select": "address,values,rowCount,columnCount"})
        values = data.get("values") or []
        return {
            "worksheet": worksheet, "address": data.get("address"),
            "rows": data.get("rowCount"), "cols": data.get("columnCount"),
            "data": values[:max_rows],
            "truncated": len(values) > max_rows,
        }

    # -- mail beheren (Mail.ReadWrite) ---------------------------------------

    _WELL_KNOWN_FOLDERS = {
        "archief": "archive", "archive": "archive", "inbox": "inbox",
        "postvak in": "inbox", "verwijderd": "deleteditems",
        "prullenbak": "deleteditems", "verzonden": "sentitems",
        "concepten": "drafts", "ongewenst": "junkemail",
    }

    def _resolve_folder(self, folder: str) -> str:
        dest = self._WELL_KNOWN_FOLDERS.get(folder.strip().lower())
        if dest:
            return dest
        nl = folder.strip().lower()
        for f in self.list_folders(top=100):
            if nl in (f.get("name") or "").lower():
                return f["id"]
        raise ValueError(f"Map '{folder}' niet gevonden.")

    def mark_read(self, message_id: str, read: bool = True) -> dict[str, Any]:
        r = requests.patch(f"{GRAPH}/me/messages/{message_id}",
                           headers=self._headers(), json={"isRead": bool(read)}, timeout=30)
        r.raise_for_status()
        return {"id": message_id, "read": bool(read)}

    def flag_message(self, message_id: str, flagged: bool = True) -> dict[str, Any]:
        status = "flagged" if flagged else "notFlagged"
        r = requests.patch(f"{GRAPH}/me/messages/{message_id}", headers=self._headers(),
                           json={"flag": {"flagStatus": status}}, timeout=30)
        r.raise_for_status()
        return {"id": message_id, "flag": status}

    def move_message(self, message_id: str, folder: str) -> dict[str, Any]:
        dest = self._resolve_folder(folder)
        res = self._post(f"/me/messages/{message_id}/move", {"destinationId": dest})
        return {"moved": True, "to": folder, "new_id": res.get("id")}

    def delete_message(self, message_id: str) -> dict[str, Any]:
        """Soft-delete: verplaatst naar Verwijderde items (herstelbaar) — nooit hard."""
        self._post(f"/me/messages/{message_id}/move", {"destinationId": "deleteditems"})
        return {"moved_to_trash": True, "id": message_id,
                "note": "Naar Verwijderde items (herstelbaar), niet permanent gewist."}

    def draft_forward(self, message_id: str, to: list[str], comment: str = "") -> dict[str, Any]:
        """Maak een DOORSTUUR-concept (verstuurt niets)."""
        draft = self._post(f"/me/messages/{message_id}/createForward", {})
        did = draft.get("id")
        payload: dict[str, Any] = {"toRecipients": [{"emailAddress": {"address": a}} for a in to]}
        r = requests.patch(f"{GRAPH}/me/messages/{did}", headers=self._headers(),
                           json=payload, timeout=30)
        r.raise_for_status()
        return {"forward_draft": True, "draft_id": did, "to": to,
                "link": r.json().get("webLink")}

    def draft_reply_all(self, message_id: str, body: str = "") -> dict[str, Any]:
        """Maak een ALLEN-BEANTWOORDEN-concept (verstuurt niets)."""
        draft = self._post(f"/me/messages/{message_id}/createReplyAll", {})
        did = draft.get("id")
        if body:
            r = requests.patch(f"{GRAPH}/me/messages/{did}", headers=self._headers(),
                               json={"body": {"contentType": "Text", "content": body}}, timeout=30)
            r.raise_for_status()
        return {"reply_all_draft": True, "draft_id": did}

    # -- schrijven (Files.ReadWrite.All / Calendars.ReadWrite) ---------------

    def excel_write(self, item_id: str, address: str, values: list[list[Any]],
                   worksheet: str | None = None) -> dict[str, Any]:
        """Schrijf waarden naar een Excel-bereik (bv. address='A1:B2',
        values=[[1,2],[3,4]]). De afmetingen van values moeten op het bereik passen."""
        from urllib.parse import quote
        if not worksheet:
            ws = self.excel_worksheets(item_id)
            worksheet = ws[0]["name"] if ws else "Blad1"
        r = requests.patch(
            f"{GRAPH}/me/drive/items/{item_id}/workbook/worksheets/{quote(worksheet)}"
            f"/range(address='{address}')",
            headers=self._headers(), json={"values": values}, timeout=30)
        r.raise_for_status()
        return {"written": True, "worksheet": worksheet, "address": address,
                "rows": len(values)}

    def create_file(self, name: str, content: Any, folder_path: str = "") -> dict[str, Any]:
        """Maak/overschrijf een bestand in OneDrive. content = str (tekst) of bytes.
        folder_path = optioneel pad in OneDrive (bv. 'Verslagen')."""
        from urllib.parse import quote
        rel = folder_path.strip("/")
        path = f"{rel}/{name}" if rel else name
        data = content.encode("utf-8") if isinstance(content, str) else content
        r = requests.put(
            f"{GRAPH}/me/drive/root:/{quote(path)}:/content",
            headers={"Authorization": f"Bearer {self._token()}"}, data=data, timeout=120)
        r.raise_for_status()
        j = r.json()
        return {"created": j.get("name"), "id": j.get("id"), "link": j.get("webUrl")}

    def export_pdf(self, item_id: str) -> bytes:
        """Converteer een Office-bestand in OneDrive naar PDF (Graph-side)."""
        from span.integrations.http import request_with_retry
        r = request_with_retry(lambda: requests.get(
            f"{GRAPH}/me/drive/items/{item_id}/content?format=pdf",
            headers={"Authorization": f"Bearer {self._token()}"}, timeout=120))
        r.raise_for_status()
        return r.content

    def respond_event(self, event_id: str, response: str, comment: str = "",
                     send_response: bool = True) -> dict[str, Any]:
        """Reageer op een afspraak-uitnodiging: response = accept | decline | tentative."""
        action = {"accept": "accept", "decline": "decline",
                  "tentative": "tentativelyAccept"}.get(response.strip().lower())
        if not action:
            raise ValueError("response moet accept, decline of tentative zijn.")
        self._post(f"/me/events/{event_id}/{action}",
                   {"comment": comment, "sendResponse": bool(send_response)}, idempotent=False)
        return {"event_id": event_id, "response": response, "sent": bool(send_response)}
