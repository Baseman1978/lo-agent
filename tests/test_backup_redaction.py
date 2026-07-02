"""WP-A2 — de brein-backup mag geen secrets/embeddings bevatten."""

from __future__ import annotations

from span.server.routes import _is_secret_prop


def test_secrets_worden_geweigerd():
    for k in ("integration_keys", "mcp_servers", "embedding", "token", "refresh",
              "access_token", "refresh_token", "client_secret", "api_key", "apiKey",
              "SPAN_AUTH_TOKEN", "some_key"):
        assert _is_secret_prop(k) is True, k


def test_gewone_content_blijft():
    for k in ("content", "name", "title", "system_prompt", "created", "updated",
              "goal", "status", "trigger", "description", "kind"):
        assert _is_secret_prop(k) is False, k
