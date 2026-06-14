"""Instelbare beveiliging (Config-node), met veilige defaults.

Bas kan een paar beschermingen afstellen in de instellingenpagina. Belangrijk:
- ontbrekende/onbekende waarde => bescherming AAN (fail-safe);
- de kern-grenzen (goedkeuringspoort voor high-risk acties, read-only
  productiedata, SSRF-blokkade op web-lezen) staan hier NIET tussen — die zijn
  absoluut en niet uitschakelbaar.
"""

from __future__ import annotations

from typing import Any

DEFAULTS = {
    "injection_scan": True,      # mail/ingest scannen op prompt-injectie
    "exfil_guard": True,         # extra poort bij uitgaand naar extern adres
    "decay_mode": "off",         # off | soft | log (geheugen-verval-ranking)
    "budget_iterations": 12,     # max tool-stappen per beurt
}


def load_security(brain: Any) -> dict[str, Any]:
    """Lees de beveiligingsinstellingen uit de Config-node; val veilig terug."""
    cfg = dict(DEFAULTS)
    try:
        rows = brain.run(
            "MATCH (c:Config {id:'runtime'}) RETURN "
            "c.sec_injection_scan AS inj, c.sec_exfil_guard AS exf, "
            "c.sec_decay_mode AS decay, c.sec_budget_iterations AS budget"
        )
    except Exception:
        return cfg
    if not rows:
        return cfg
    r = rows[0]
    if r.get("inj") is not None:
        cfg["injection_scan"] = bool(r["inj"])
    if r.get("exf") is not None:
        cfg["exfil_guard"] = bool(r["exf"])
    if r.get("decay") in ("off", "soft", "log"):
        cfg["decay_mode"] = r["decay"]
    if isinstance(r.get("budget"), int) and 3 <= r["budget"] <= 40:
        cfg["budget_iterations"] = r["budget"]
    return cfg


def save_security(brain: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Sla alleen de instelbare velden op; valideer streng. Geeft de actieve
    config terug."""
    sets, params = [], {}
    if "injection_scan" in body:
        sets.append("c.sec_injection_scan = $inj")
        params["inj"] = bool(body["injection_scan"])
    if "exfil_guard" in body:
        sets.append("c.sec_exfil_guard = $exf")
        params["exf"] = bool(body["exfil_guard"])
    if body.get("decay_mode") in ("off", "soft", "log"):
        sets.append("c.sec_decay_mode = $decay")
        params["decay"] = body["decay_mode"]
    if "budget_iterations" in body:
        try:
            n = int(body["budget_iterations"])
            if 3 <= n <= 40:
                sets.append("c.sec_budget_iterations = $budget")
                params["budget"] = n
        except (TypeError, ValueError):
            pass
    if sets:
        brain.run("MERGE (c:Config {id:'runtime'}) SET " + ", ".join(sets), **params)
    return load_security(brain)
