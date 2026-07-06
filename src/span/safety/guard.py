"""Tool-guard: risico-handhaving + exfiltratie-check bij dispatch (F1.1/F1.2).

Centrale beoordeling vóór een tool draait. Geeft een besluit terug:
- allow: tool mag direct draaien.
- approval: forceer de AgentInbox-poort (ook als autonomy 'auto' staat).
- block: weiger (bv. high/crit zonder enige goedkeuringsweg).
"""

import re
from typing import Any

from span.safety.risk import risk_for

# Tools die data naar buiten sturen (potentieel exfiltratie-kanaal). draft_reply
# verstuurt niets (concept) en zit hier bewust NIET in — opname zou schijn-
# veiligheid geven omdat de recipient niet bekend is bij dispatch.
_OUTBOUND = {"o365_mail_send", "o365_event_create", "o365_mail_forward_send"}
# Tools die hun eigen goedkeuringspoort (queue) hebben en een 'approval'-besluit
# zélf honoreren. Een high/crit-tool die hier NIET in staat en 'approval' krijgt,
# wordt geblokkeerd — de guard dwingt af, hij adviseert niet alleen. MCP-schrijf-
# tools (mcp__…) hebben sinds de MCP-koppeling ook een queue-pad (zie dispatch).
_HAS_QUEUE_PATH = {"o365_mail_send", "o365_event_create",
                   "o365_event_update", "o365_event_delete", "o365_event_cancel",
                   "o365_event_respond", "o365_todo_delete",
                   "o365_mail_reply_send", "o365_mail_forward_send",
                   "o365_file_delete", "o365_file_share_link",
                   "asana_task_delete", "asana_comment_add",
                   "fireflies_meeting_delete"}


def _has_queue_path(name: str) -> bool:
    return name in _HAS_QUEUE_PATH or name.startswith("mcp__")
_INTERNAL_DOMAIN = "lomans.nl"


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw]                      # HOOG-1: string -> lijst, geen char-iteratie
    return [str(r) for r in (raw or [])]


def _recipients(name: str, args: dict[str, Any]) -> list[str]:
    if name == "o365_mail_send":
        # cc/bcc tellen mee: ook een extern cc-adres is een uitgaand kanaal
        return (_as_list(args.get("to")) + _as_list(args.get("cc"))
                + _as_list(args.get("bcc")))
    if name == "o365_mail_forward_send":
        return _as_list(args.get("to"))
    return _as_list(args.get("attendees"))


def _extract_addr(r: str) -> str:
    """Pak het e-mailadres uit 'Naam <adres>' of een kale string."""
    m = re.search(r"<([^>]+)>", r)
    return (m.group(1) if m else r).strip().lower()


def _is_internal(r: str) -> bool:
    """Fail-closed: alleen een aantoonbaar @lomans.nl-adres is intern.
    Geen herkenbaar adres (bv. kale displaynaam) => behandeld als extern."""
    addr = _extract_addr(r)
    return bool(re.search(r"@lomans\.nl$", addr))


def _has_external_recipient(name: str, args: dict[str, Any]) -> bool:
    rcpts = _recipients(name, args)
    if not rcpts:
        return False
    return any(not _is_internal(r) for r in rcpts)


def assess_tool(name: str, args: dict[str, Any], *, autonomy_auto: bool,
                has_inbox: bool, exfil_guard: bool = True) -> dict[str, Any]:
    """Beslis of een tool-call mag, moet wachten op goedkeuring, of geweigerd
    wordt. Retourneert {decision, tier, reason}. De guard is BINDEND.

    exfil_guard kan in de instellingen uit (dan vertrouwt Span volledig op de
    autonomy-stand); de high/crit-poort blijft ALTIJD staan."""
    tier = risk_for(name)

    # exfiltratie-vangnet (HOOG-3): ELK uitgaand bericht naar een extern adres
    # gaat altijd via de poort — ook bij autonomy=auto, ongeacht grootte. Een
    # gericht lek (wachtwoord, klantnummer) is klein; grootte is geen drempel.
    if exfil_guard and name in _OUTBOUND and _has_external_recipient(name, args):
        if not has_inbox:
            return {"decision": "block", "tier": tier,
                    "reason": "uitgaand naar extern adres zonder goedkeuringspoort"}
        return {"decision": "approval", "tier": tier,
                "reason": "uitgaand naar extern adres — altijd eerst goedkeuren"}

    if tier in ("high", "crit"):
        if autonomy_auto and name in _HAS_QUEUE_PATH:
            return {"decision": "allow", "tier": tier, "reason": "autonomy=auto"}
        if has_inbox and _has_queue_path(name):
            return {"decision": "approval", "tier": tier,
                    "reason": "high-risk tool via de Agent Inbox"}
        # high/crit zonder eigen queue-pad: niet zomaar uitvoeren (bindend)
        return {"decision": "block", "tier": tier,
                "reason": "high-risk tool zonder goedkeuringsweg"}

    return {"decision": "allow", "tier": tier, "reason": "low/med"}
