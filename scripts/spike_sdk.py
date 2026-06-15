"""WP-1 spike — verifieer de drie niet-uit-docs-bevestigde SDK-punten.

Draai dit op een machine waar je bent ingelogd op je Claude-abonnement:
    pip install claude-agent-sdk
    claude setup-token          # -> CLAUDE_CODE_OAUTH_TOKEN (1 jaar geldig)
    # ZORG dat ANTHROPIC_API_KEY NIET in de env staat (die wint altijd)
    set CLAUDE_CODE_OAUTH_TOKEN=...        (Windows)  /  export ... (bash)
    python scripts/spike_sdk.py

Het meet/print:
  1) streaming-granulariteit  — komen er fijnmazige tekst-deltas (per token) of grote brokken?
  2) bindende deny            — blokkeert can_use_tool een tool-call écht?
  3) fout-vorm bij credit-op  — welke exception/HTTP-status (de spil van de API-backup-router)

Raakt GEEN Span-code; puur een wegwerp-meting. Resultaten -> werkplan WP-3/WP-5.
"""
from __future__ import annotations

import asyncio
import os
import time


def _check_env():
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("WAARSCHUWING: ANTHROPIC_API_KEY staat in de env — die WINT van het "
              "abonnement. Unset 'm om de abonnements-credit te testen.\n")
    print("OAuth-token aanwezig:", bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), "\n")


async def main():
    _check_env()
    try:
        from claude_agent_sdk import (
            ClaudeSDKClient, ClaudeAgentOptions, tool, create_sdk_mcp_server,
        )
    except Exception as e:
        print("claude-agent-sdk niet geïnstalleerd of import-fout:", repr(e))
        print("Doe: pip install claude-agent-sdk")
        return

    # --- 1 + 3: streaming-granulariteit + fout-vorm -----------------------
    print("=== 1) streaming-granulariteit ===")
    deltas, t0 = [], time.time()
    try:
        opts = ClaudeAgentOptions(
            system_prompt="Je bent een testorb. Antwoord in 2 zinnen.",
            include_partial_messages=True,
            max_turns=1,
        )
        async with ClaudeSDKClient(options=opts) as client:
            await client.query("Noem kort drie kleuren en waarom je ze mooi vindt.")
            async for msg in client.receive_response():
                # we loggen het type + (als aanwezig) de tekst-delta-grootte
                name = type(msg).__name__
                txt = getattr(msg, "text", None) or getattr(getattr(msg, "delta", None), "text", None)
                if txt:
                    deltas.append(len(txt))
                else:
                    blocks = getattr(msg, "content", None)
                    if isinstance(blocks, list):
                        for b in blocks:
                            bt = getattr(b, "text", None)
                            if bt:
                                deltas.append(len(bt))
        dt = time.time() - t0
        print(f"deltas ontvangen: {len(deltas)} | groottes(eerste 12): {deltas[:12]} | {dt:.1f}s")
        print("-> fijnmazig (per-token) als veel kleine deltas; grof als enkele grote.\n")
    except Exception as e:
        print("FOUT tijdens streaming-test (mogelijk de credit-op/auth-fout — noteer dit!):")
        print("   type:", type(e).__name__)
        print("   repr:", repr(e)[:400], "\n")

    # --- 2: bindende deny via can_use_tool --------------------------------
    print("=== 2) bindende deny (can_use_tool) ===")

    @tool("verboden_actie", "Een testtool die NOOIT mag draaien.", {"x": str})
    async def verboden_actie(args):
        return {"content": [{"type": "text", "text": "DIT HAD NIET MOGEN DRAAIEN"}]}

    span_srv = create_sdk_mcp_server("spike", "1.0", [verboden_actie])
    blocked = {"hit": False, "denied": False}

    async def can_use_tool(tool_name, tool_input, ctx):
        from claude_agent_sdk import PermissionResultDeny
        blocked["denied"] = True
        return PermissionResultDeny(message="geweigerd door de spike-gate")

    try:
        opts2 = ClaudeAgentOptions(
            mcp_servers={"spike": span_srv},
            allowed_tools=["mcp__spike__verboden_actie"],
            can_use_tool=can_use_tool,
            permission_mode="default",
            max_turns=2,
        )
        async with ClaudeSDKClient(options=opts2) as client:
            await client.query("Roep de tool 'verboden_actie' aan met x='test'.")
            async for msg in client.receive_response():
                blocks = getattr(msg, "content", None)
                if isinstance(blocks, list):
                    for b in blocks:
                        if "HAD NIET MOGEN DRAAIEN" in (getattr(b, "text", "") or ""):
                            blocked["hit"] = True
        print(f"can_use_tool aangeroepen: {blocked['denied']} | tool tóch uitgevoerd: {blocked['hit']}")
        print("-> gewenst: denied=True, uitgevoerd=False (deny is bindend).\n")
    except Exception as e:
        print("FOUT tijdens deny-test:", type(e).__name__, repr(e)[:300], "\n")

    print("=== klaar — noteer streaming-granulariteit, deny-uitkomst en (indien geraakt) de credit-op-foutvorm ===")


if __name__ == "__main__":
    asyncio.run(main())
