"""Integration Broker — één interne laag waarmee LO externe apps aanbiedt.

De rest van LO praat nooit rechtstreeks met een provider (Nango, MCP, Graph);
alles loopt via de broker, die onder LO's bestaande governance hangt:
risico-poort (assess_tool) -> Agent Inbox (approval) -> egress-allowlist -> audit.

Een integratie toevoegen = één declaratieve `Connector` in het register
(`connectors.py`). De broker + adapters doen de rest (catalogus, koppelen,
acties uitvoeren). Zie HOE-VOEG-IK-EEN-INTEGRATIE-TOE.md.
"""

from span.integrations.broker.broker import IntegrationBroker, build_broker

__all__ = ["IntegrationBroker", "build_broker"]
