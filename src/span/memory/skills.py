"""Skills — herbruikbare werkwijzen (①) en uitvoerbare tool-macro's (②).

Een Skill is een graph-node met:
  name        unieke kebab-case-naam
  description waarvoor het is
  trigger     wanneer in te zetten
  kind        'workflow' (instructie-tekst) of 'macro' (reeks tool-stappen)
  body        instructie-tekst (workflow)
  steps       JSON-lijst [{tool, args, save?}, ...] (macro)
  params      JSON-lijst gedeclareerde invoer-namen
  author      'user' (door Bas in de HUD) of 'agent' (door LO voorgesteld)
  enabled     False zolang een agent-voorstel nog niet is goedgekeurd
  usage_count hoe vaak ingezet

Veiligheid: een macro voert ALLEEN bestaande tools uit, elk via de normale
dispatch -> de bestaande risico-poort/Agent Inbox blijft per stap gelden.
Door de agent gemaakte skills zijn enabled=False tot Bas ze goedkeurt.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
MAX_MACRO_STEPS = 12


def normalize_name(name: str) -> str:
    n = (name or "").strip().lower().replace(" ", "-")
    n = re.sub(r"[^a-z0-9-]", "", n)
    return n[:48]


def _row_to_skill(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": r.get("name"),
        "description": r.get("description") or "",
        "trigger": r.get("trigger") or "",
        "kind": r.get("kind") or "workflow",
        "body": r.get("body") or "",
        "steps": json.loads(r["steps"]) if r.get("steps") else [],
        "params": json.loads(r["params"]) if r.get("params") else [],
        "author": r.get("author") or "user",
        "enabled": bool(r.get("enabled", True)),
        "usage_count": int(r.get("usage_count") or 0),
        "shared": bool(r.get("shared", False)),
    }


def list_skills(brain: Any, shared: Any = None, include_disabled: bool = True) -> list[dict[str, Any]]:
    rows = brain.run(
        """
        MATCH (sk:Skill)
        RETURN sk.name AS name, sk.description AS description, sk.trigger AS trigger,
               sk.kind AS kind, sk.body AS body, sk.steps AS steps, sk.params AS params,
               sk.author AS author, coalesce(sk.enabled, true) AS enabled,
               coalesce(sk.usage_count, 0) AS usage_count
        ORDER BY coalesce(sk.usage_count, 0) DESC, sk.name
        """
    )
    skills = [_row_to_skill(r) for r in rows]
    if not include_disabled:
        skills = [s for s in skills if s["enabled"]]
    if shared is not None:
        have = {s["name"] for s in skills}
        try:
            srows = shared.run(
                "MATCH (sk:Skill) WHERE coalesce(sk.enabled, true) "
                "RETURN sk.name AS name, sk.description AS description, sk.trigger AS trigger, "
                "sk.kind AS kind, sk.body AS body, sk.steps AS steps, sk.params AS params, "
                "sk.author AS author, true AS enabled, coalesce(sk.usage_count,0) AS usage_count"
            )
            for r in srows:
                if r["name"] not in have:
                    s = _row_to_skill(r); s["shared"] = True
                    skills.append(s)
        except Exception:
            pass
    return skills


def get_skill(brain: Any, name: str) -> dict[str, Any] | None:
    rows = brain.run(
        """
        MATCH (sk:Skill {name:$name})
        RETURN sk.name AS name, sk.description AS description, sk.trigger AS trigger,
               sk.kind AS kind, sk.body AS body, sk.steps AS steps, sk.params AS params,
               sk.author AS author, coalesce(sk.enabled, true) AS enabled,
               coalesce(sk.usage_count, 0) AS usage_count
        """,
        name=name,
    )
    return _row_to_skill(rows[0]) if rows else None


def upsert_skill(brain: Any, *, name: str, description: str = "", trigger: str = "",
                 kind: str = "workflow", body: str = "", steps: Any = None,
                 params: Any = None, author: str = "user", enabled: bool = True) -> dict[str, Any]:
    name = normalize_name(name)
    if not NAME_RE.match(name):
        raise ValueError("Ongeldige skill-naam (gebruik kebab-case, 2-48 tekens).")
    if kind not in ("workflow", "macro"):
        raise ValueError("kind moet 'workflow' of 'macro' zijn.")
    steps_json = json.dumps(steps or [], ensure_ascii=False)
    params_json = json.dumps(params or [], ensure_ascii=False)
    if kind == "macro":
        validate_steps(steps or [])
    brain.run(
        """
        MERGE (sk:Skill {name:$name})
        ON CREATE SET sk.created = datetime(), sk.usage_count = 0
        SET sk.description=$description, sk.trigger=$trigger, sk.kind=$kind,
            sk.body=$body, sk.steps=$steps, sk.params=$params, sk.author=$author,
            sk.enabled=$enabled, sk.updated = datetime()
        """,
        name=name, description=description, trigger=trigger, kind=kind, body=body,
        steps=steps_json, params=params_json, author=author, enabled=bool(enabled),
    )
    return {"name": name, "kind": kind, "enabled": bool(enabled)}


def set_enabled(brain: Any, name: str, enabled: bool) -> bool:
    rows = brain.run(
        "MATCH (sk:Skill {name:$name}) SET sk.enabled=$enabled, sk.updated=datetime() "
        "RETURN sk.name AS name",
        name=name, enabled=bool(enabled),
    )
    return bool(rows)


def delete_skill(brain: Any, name: str) -> bool:
    rows = brain.run(
        "MATCH (sk:Skill {name:$name}) DETACH DELETE sk RETURN 1 AS ok",
        name=name,
    )
    return bool(rows)


def validate_steps(steps: list[dict[str, Any]]) -> None:
    if not isinstance(steps, list) or not steps:
        raise ValueError("Een macro heeft minstens één stap nodig.")
    if len(steps) > MAX_MACRO_STEPS:
        raise ValueError(f"Te veel stappen (max {MAX_MACRO_STEPS}).")
    for i, st in enumerate(steps):
        if not isinstance(st, dict) or not st.get("tool"):
            raise ValueError(f"Stap {i + 1} mist een 'tool'.")
        t = str(st["tool"])
        if t.startswith("skill_") or t.startswith("use_skill"):
            raise ValueError("Een skill mag geen andere skill aanroepen (geen recursie).")
        if "args" in st and not isinstance(st["args"], dict):
            raise ValueError(f"Stap {i + 1}: 'args' moet een object zijn.")


def _resolve(value: Any, params: dict[str, Any]) -> Any:
    """Vervang {{params.NAAM}} in string-argumenten door de meegegeven waarde."""
    if isinstance(value, str):
        def sub(m):
            key = m.group(1).strip()
            if key.startswith("params."):
                return str(params.get(key[len("params."):], ""))
            return m.group(0)
        return re.sub(r"\{\{([^}]+)\}\}", sub, value)
    if isinstance(value, dict):
        return {k: _resolve(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, params) for v in value]
    return value


def execute_macro(skill: dict[str, Any], params: dict[str, Any],
                  dispatch: Callable[[str, dict[str, Any]], str],
                  known_tools: set[str] | None = None) -> dict[str, Any]:
    """Voer de stappen uit via de normale dispatch (elke stap gaat door de
    veiligheidspoort). Param-templating met {{params.X}}. Resultaten worden
    verzameld en teruggegeven zodat het model ze kan gebruiken."""
    steps = skill.get("steps") or []
    validate_steps(steps)
    results: list[dict[str, Any]] = []
    for i, st in enumerate(steps):
        tool = str(st["tool"])
        if known_tools is not None and tool not in known_tools:
            results.append({"step": i + 1, "tool": tool,
                            "error": "tool niet beschikbaar (integratie uit of onbekend)"})
            continue
        args = _resolve(st.get("args") or {}, params)
        try:
            raw = dispatch(tool, args)
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            results.append({"step": i + 1, "tool": tool, "result": parsed})
        except Exception as exc:  # nooit de hele macro laten crashen
            results.append({"step": i + 1, "tool": tool, "error": f"{type(exc).__name__}: {exc}"})
    return {"skill": skill["name"], "kind": "macro", "steps_run": len(results), "results": results}


def render_for_prompt(skills: list[dict[str, Any]]) -> str:
    """Korte lijst voor de system-prompt (alleen ingeschakelde skills)."""
    lines = []
    for s in skills:
        if not s.get("enabled", True):
            continue
        tag = "⚙ uitvoerbaar" if s.get("kind") == "macro" else "werkwijze"
        trig = f" (trigger: {s['trigger']})" if s.get("trigger") else ""
        team = " [team]" if s.get("shared") else ""
        lines.append(f"- {s['name']} · {tag}{team}: {s.get('description', '')}{trig}")
    return "\n".join(lines)
