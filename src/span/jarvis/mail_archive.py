"""Mailmap archiveren naar het geheugen via de MCP-M365-server.

Lost de frustratie op dat Span met losse MCP-tools en parameter-details moest
puzzelen om mails uit een Outlook-map te halen. Eén betrouwbare functie:
vind de map op naam, haal de berichten in batches met de juiste folder_id,
en schrijf per mail één beknopt MemoryFragment (afzender, datum, onderwerp,
korte kern) — niet de volledige body (te groot bij duizenden mails).

Idempotent: elke mail krijgt een mail_graph_id-property; al gearchiveerde
mails worden overgeslagen, dus herhaald aanroepen vult de map veilig verder.
"""

from __future__ import annotations

import json
from typing import Any


def _find_tool(mcp: Any, suffix: str) -> str | None:
    for n in mcp.tool_names():
        if n.endswith("__" + suffix):
            return n
    return None


def _value(mcp: Any, tool: str, args: dict[str, Any]) -> tuple[list[dict], str]:
    res = mcp.call(tool, args)
    if res.get("error"):
        return [], res["error"]
    try:
        data = json.loads(res.get("text", "") or "{}")
        return (data.get("value", []) if isinstance(data, dict) else []), ""
    except json.JSONDecodeError:
        return [], "kon antwoord niet lezen"


def find_folder(mcp: Any, name: str) -> dict[str, Any] | None:
    """Vind een mailmap op (deel van de) displayName, hoofdletterongevoelig."""
    tool = _find_tool(mcp, "m365_mail_folders")
    if not tool:
        return None
    folders, _ = _value(mcp, tool, {})
    nl = name.strip().lower()
    # exacte match eerst, anders 'bevat'
    for f in folders:
        if (f.get("displayName") or "").strip().lower() == nl:
            return f
    for f in folders:
        if nl in (f.get("displayName") or "").lower():
            return f
    return None


def archive_folder(mcp: Any, brain: Any, fragments: Any, session_id: str,
                   folder_name: str, limit: int = 200, batch: int = 50) -> dict[str, Any]:
    """Archiveer tot `limit` nog niet opgeslagen mails uit `folder_name`.
    Idempotent; geef de voortgang terug zodat je het kunt herhalen."""
    if mcp is None:
        return {"error": "Geen MCP-server met M365 gekoppeld."}
    folder = find_folder(mcp, folder_name)
    if folder is None:
        return {"error": f"Map '{folder_name}' niet gevonden."}
    msg_tool = _find_tool(mcp, "m365_mail_folder_messages")
    if not msg_tool:
        return {"error": "MCP-tool m365_mail_folder_messages ontbreekt."}

    fid = folder["id"]
    total_in_folder = folder.get("totalItemCount", 0)
    archived = skipped = 0
    skip = 0
    # eindige cap op basis van de mapgrootte: kan de hele map door (ook bij een
    # herhaalde vul-run die door al-gearchiveerde mails heen skipt), maar nooit
    # oneindig — ook niet als de server 'skip' zou negeren
    max_batches = (max(total_in_folder, limit) // batch) + 3
    for _ in range(max_batches):
        if archived >= limit:
            break
        mails, err = _value(mcp, msg_tool, {"folder_id": fid, "top": batch, "skip": skip})
        if err:
            return {"error": f"Ophalen mislukt: {err}", "archived": archived}
        if not mails:
            break  # einde van de map
        skip += len(mails)
        for m in mails:
            if archived >= limit:
                break
            gid = m.get("id")
            if not gid:
                continue
            # idempotent: al gearchiveerd?
            exists = brain.run(
                "MATCH (mf:MemoryFragment {mail_graph_id:$g}) RETURN count(mf) AS n",
                g=gid)
            if exists and exists[0]["n"] > 0:
                skipped += 1
                continue
            frm = ((m.get("from") or {}).get("emailAddress") or {})
            sender = frm.get("name") or frm.get("address") or "onbekend"
            subject = m.get("subject") or "(zonder onderwerp)"
            date = (m.get("receivedDateTime") or "")[:10]
            preview = (m.get("bodyPreview") or "").strip().replace("\r", " ").replace("\n", " ")[:300]
            content = f"Mail van {sender} ({date}): {subject}. {preview}"
            # mail-inhoud is door-derden-bestuurbaar -> untrusted ingest met
            # injectie-scan; mail_graph_id atomair in dezelfde transactie (M19).
            # De UNIQUE-constraint op mail_graph_id vangt een race af: bij een
            # gelijktijdige dubbele schrijf telt het als al-bekend, niet als fout.
            try:
                fragments.write_external(
                    mf_type="observation", content=content, session_id=session_id,
                    source="mail", context=f"mailarchief/{folder_name}", scope="werk",
                    extra_props={"mail_graph_id": gid, "event_date": date})
                archived += 1
            except Exception:
                skipped += 1
        if len(mails) < batch:
            break  # einde van de map
    return {"folder": folder.get("displayName"), "total_in_folder": total_in_folder,
            "archived": archived, "skipped_already_known": skipped,
            "done": archived + skipped >= total_in_folder}


def find_folder_native(o365: Any, name: str) -> dict[str, Any] | None:
    """Vind een mailmap op (deel van de) naam, hoofdletterongevoelig — via O365Client."""
    nl = name.strip().lower()
    folders = o365.list_folders(top=100)
    for f in folders:
        if (f.get("name") or "").strip().lower() == nl:
            return f
    for f in folders:
        if nl in (f.get("name") or "").lower():
            return f
    return None


def archive_folder_native(o365: Any, brain: Any, fragments: Any, session_id: str,
                          folder_name: str, limit: int = 200, batch: int = 50,
                          since_days: int = 365) -> dict[str, Any]:
    """Archiveer tot `limit` nog niet opgeslagen mails uit `folder_name` naar het
    geheugen, via het app-login-token (géén MCP). Datum-gefilterd op de laatste
    `since_days`. Idempotent op mail_graph_id — herhaald aanroepen vult veilig
    verder tot 'done'. Schrijft per mail één beknopt MemoryFragment."""
    folder = find_folder_native(o365, folder_name)
    if folder is None:
        return {"error": f"Map '{folder_name}' niet gevonden.",
                "beschikbare_mappen": [f.get("name") for f in o365.list_folders(top=100)][:40]}
    fid = folder["id"]
    total_in_folder = folder.get("total", 0)
    archived = skipped = skip = 0
    max_batches = (max(total_in_folder, limit) // batch) + 3
    for _ in range(max_batches):
        if archived >= limit:
            break
        mails = o365.folder_messages(fid, top=batch, skip=skip, since_days=since_days)
        if not mails:
            break
        skip += len(mails)
        for m in mails:
            if archived >= limit:
                break
            gid = m.get("graph_id")
            if not gid:
                continue
            exists = brain.run(
                "MATCH (mf:MemoryFragment {mail_graph_id:$g}) RETURN count(mf) AS n", g=gid)
            if exists and exists[0]["n"] > 0:
                skipped += 1
                continue
            sender = m.get("from") or "onbekend"
            subject = m.get("subject") or "(zonder onderwerp)"
            date = (m.get("received") or "")[:10]
            preview = (m.get("preview") or "").replace("\r", " ").replace("\n", " ")[:300]
            att = " [met bijlage]" if m.get("has_attachments") else ""
            content = f"Mail van {sender} ({date}): {subject}{att}. {preview}"
            try:
                fragments.write_external(
                    mf_type="observation", content=content, session_id=session_id,
                    source="mail", context=f"mailarchief/{folder_name}", scope="werk",
                    extra_props={"mail_graph_id": gid, "event_date": date})
                archived += 1
            except Exception:
                skipped += 1
        if len(mails) < batch:
            break
    return {"folder": folder.get("name"), "total_in_folder": total_in_folder,
            "archived": archived, "skipped_already_known": skipped,
            "done": archived + skipped >= total_in_folder,
            "tip": "Roep nogmaals aan om verder te vullen tot done=true."}
