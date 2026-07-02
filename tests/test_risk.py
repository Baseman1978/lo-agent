"""WP-B1 — MCP-tools: read-only-allowlist met fail-closed default (audit C2)."""

from __future__ import annotations

from span.safety.risk import risk_for, mcp_capability


class TestMcpRisk:
    def test_destructieve_tools_vereisen_approval(self):
        for t in ["mcp__github__merge_pull_request", "mcp__notion__archive_page",
                  "mcp__admin__purge_user", "mcp__ci__deploy_release",
                  "mcp__slack__revoke_token", "mcp__jira__cancel_sprint",
                  "mcp__drive__trash_file", "mcp__x__set_permissions",
                  "mcp__gh__close_issue", "mcp__n__update-page",
                  "mcp__n__delete_block", "mcp__n__notion-create-comment"]:
            assert mcp_capability(t) == "write", t
            assert risk_for(t) == "high", t

    def test_lees_tools_mogen_direct(self):
        for t in ["mcp__notion__notion-search", "mcp__fireflies__fireflies_get_transcript",
                  "mcp__gh__list_repos", "mcp__n__query_data_sources",
                  "mcp__x__describe_table", "mcp__f__fireflies_get_summary"]:
            assert mcp_capability(t) == "read", t
            assert risk_for(t) == "med", t

    def test_onbekend_verb_is_failclosed_write(self):
        assert mcp_capability("mcp__x__frobnicate_thing") == "write"
        assert risk_for("mcp__x__frobnicate_thing") == "high"

    def test_niet_mcp_ongewijzigd(self):
        assert risk_for("o365_mail_send") == "high"
        assert risk_for("brain_search") == "low"
        assert risk_for("o365_mail_search") == "low"
