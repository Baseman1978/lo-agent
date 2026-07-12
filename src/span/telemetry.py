"""A1 — beurt-telemetrie: gesegmenteerde latency-meting als meetlat voor de
bewijs-gepoorte fase B.

Append-only JSONL, thread-safe, best-effort: een telemetrie-fout mag NOOIT een
gespreksbeurt breken. Segmenten: stt (spraak->tekst), llm (model-generatie),
tool (tool-executie), tts (tekst->eerste-klank), turn (end-to-end). De
aggregatie beantwoordt de poort-vraag "welk segment domineert?" — zonder die
cijfers is de A->B-poort blind.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from span.config import PROJECT_ROOT

_lock = threading.Lock()
_MAX_BYTES = 5_000_000  # ~5 MB -> roteer naar .prev; houdt het bestand begrensd
_MAX_TAIL = 20_000      # aggregate leest hoogstens zoveel recente regels


def _enabled() -> bool:
    val = os.environ.get("SPAN_TELEMETRY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def _path() -> Path:
    override = os.environ.get("SPAN_TELEMETRY_FILE", "").strip()
    return Path(override) if override else PROJECT_ROOT / "data" / "telemetry.jsonl"


def record(seg: str, ms: float, meta: dict[str, Any] | None = None) -> None:
    """Schrijf één segment-meting weg. Best-effort: slikt elke fout in."""
    if not _enabled():
        return
    row: dict[str, Any] = {"ts": time.time(), "seg": seg, "ms": round(float(ms), 1)}
    if meta:
        row["meta"] = meta
    try:
        p = _path()
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > _MAX_BYTES:
                p.replace(p.with_suffix(p.suffix + ".prev"))
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # telemetrie is best-effort; nooit de beurt breken


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 1)


def aggregate(window_s: float = 86400.0) -> dict[str, Any]:
    """Per-segment count/p50/p95/max over de laatste `window_s` seconden."""
    p = _path()
    cutoff = time.time() - window_s
    try:
        with _lock:
            lines = p.read_text(encoding="utf-8").splitlines()[-_MAX_TAIL:] if p.exists() else []
    except Exception:
        lines = []
    buckets: dict[str, list[float]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if float(row.get("ts", 0)) < cutoff:
            continue
        buckets.setdefault(str(row.get("seg", "?")), []).append(float(row.get("ms", 0)))
    segments: dict[str, Any] = {}
    for seg, vals in buckets.items():
        vals.sort()
        segments[seg] = {
            "count": len(vals),
            "p50": _percentile(vals, 0.50),
            "p95": _percentile(vals, 0.95),
            "max": round(max(vals), 1),
        }
    return {"window_s": window_s, "segments": segments}
