"""Span — een AI die zichzelf onthoudt.

Gebouwd voor Bas Spaan. Graph-as-brain architectuur:
Neo4j knowledge graph als geheugen, ORQ.AI als LLM-gateway,
een orchestrator die de juiste laag op het juiste moment aanspreekt.
"""

__version__ = "0.1.0"
# configureerbare agentnaam (zelfde bron als de HUD/branding); default "LO"
import os as _os
AGENT_NAME = _os.environ.get("AGENT_NAME", "LO").strip() or "LO"
