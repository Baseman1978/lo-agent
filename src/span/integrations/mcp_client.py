"""MCP-client — Span praat met externe Model Context Protocol-servers.

Minimalistische streamable-HTTP-client (JSON-RPC 2.0): initialize, tools/list,
tools/call. Geen externe SDK (ARM64-veilig, pure requests). De server kan
antwoorden met application/json of met een SSE-stream (text/event-stream); we
parsen beide. Een Mcp-Session-Id uit de initialize-respons reist mee.

Auth: een Bearer access_token (via OAuth, zie mcp_oauth.py) of een statische
token. Untrusted output (tool-resultaten) hoort door de quarantaine-laag.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import requests

from span.safety.egress import EgressBlocked, allow_host, host_allowed

PROTOCOL_VERSION = "2025-06-18"
MAX_RPC_BYTES = 5_000_000   # harde cap tegen een defecte/kwaadaardige server (M6)


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, url: str, token: str = "", timeout: float = 30.0):
        self._url = url
        self._token = token
        self._timeout = timeout
        self._session_id = ""
        self._next_id = 0

    def set_token(self, token: str) -> None:
        self._token = token

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _rpc(self, method: str, params: dict[str, Any] | None = None,
             notify: bool = False) -> Any:
        # egress-poort: de MCP-URL moet https zijn en op de allowlist staan
        # (de host wordt bij bewust koppelen geregistreerd). Voorkomt dat een
        # gemanipuleerde config Span naar een vreemde/interne host laat POST'en.
        if urlparse(self._url).scheme != "https" or not host_allowed(urlparse(self._url).hostname or ""):
            raise EgressBlocked(f"MCP-URL niet toegestaan: {self._url}")
        self._next_id += 1
        req_id = self._next_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = req_id
        resp = requests.post(self._url, headers=self._headers(),
                             json=body, timeout=self._timeout, stream=True)
        if resp.status_code == 401:
            raise MCPError("unauthorized")  # token verlopen/ontbreekt -> opnieuw inloggen
        resp.raise_for_status()
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        if notify:
            resp.close()
            return None
        return _parse_result(resp, req_id)

    def initialize(self) -> dict[str, Any]:
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Span", "version": "1.0"},
        })
        # de spec vereist een 'initialized'-notificatie na initialize
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except Exception:
            pass
        return result or {}

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {}) or {}
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        # MCP tool-resultaat: content-blocks; vlak ze af tot tekst
        parts = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(f"[{block.get('type')}]")
        return {"text": "\n".join(parts), "isError": bool(result.get("isError"))}


def load_servers(brain: Any) -> list[dict[str, Any]]:
    """Geconfigureerde MCP-servers uit de Config-node (JSON-string)."""
    try:
        rows = brain.run(
            "MATCH (c:Config {id:'runtime'}) RETURN c.mcp_servers AS s")
    except Exception:
        return []
    raw = (rows[0].get("s") if rows else None) or "[]"
    try:
        servers = json.loads(raw)
        return servers if isinstance(servers, list) else []
    except json.JSONDecodeError:
        return []


def save_servers(brain: Any, servers: list[dict[str, Any]]) -> None:
    brain.run("MERGE (c:Config {id:'runtime'}) SET c.mcp_servers = $s",
              s=json.dumps(servers))


class MCPRegistry:
    """Beheert de verbonden MCP-servers en levert hun tools als Span-tools.

    Tool-namen krijgen het voorvoegsel mcp__<server>__<tool>. Een onbereikbare
    of niet-ingelogde server faalt zacht: hij levert simpelweg geen tools en
    blokkeert Span niet. Output van tool-calls is untrusted (de aanroeper
    quarantained het)."""

    def __init__(self, servers: list[dict[str, Any]], brain: Any = None):
        self._clients: dict[str, MCPClient] = {}
        self._specs: list[dict[str, Any]] = []
        self._servers = {s.get("name"): dict(s) for s in servers if s.get("name")}
        self._brain = brain          # om een ververst token te kunnen opslaan
        # bewust-gekoppelde MCP-hosts op de runtime-allowlist zetten zodat hun
        # eigen RPC/OAuth-calls de egress-poort passeren (vreemde hosts niet)
        for s in servers:
            if s.get("url"):
                allow_host(urlparse(s["url"]).hostname or "")
        for s in servers:
            name, url, token = s.get("name"), s.get("url"), s.get("token", "")
            if not name or not url or not token:
                continue  # niet-ingelogde server overslaan
            try:
                client = MCPClient(url, token)
                client.initialize()
                tools = client.list_tools()
            except MCPError as exc:
                if str(exc) == "unauthorized" and self._try_refresh(name):
                    client = self._clients.get(name) or MCPClient(url, self._servers[name]["token"])
                    self._clients[name] = client
                    try:
                        client.set_token(self._servers[name]["token"])
                        client.initialize()
                        tools = client.list_tools()
                    except Exception as exc2:
                        print(f"[mcp] '{name}' na refresh nog niet bereikbaar: {exc2}", flush=True)
                        continue
                else:
                    print(f"[mcp] server '{name}' niet ingelogd/bereikbaar: {exc}", flush=True)
                    continue
            except Exception as exc:
                print(f"[mcp] server '{name}' niet bereikbaar: {exc}", flush=True)
                continue
            self._clients[name] = client
            for t in tools:
                full = f"mcp__{name}__{t.get('name')}"
                self._specs.append({
                    "type": "function",
                    "function": {
                        "name": full,
                        "description": f"[{name}] " + (t.get("description") or t.get("name") or ""),
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                })

    def tool_specs(self) -> list[dict[str, Any]]:
        return list(self._specs)

    def tool_names(self) -> list[str]:
        return [s["function"]["name"] for s in self._specs]

    def _try_refresh(self, name: str) -> bool:
        """Ververs het access_token van een server via zijn refresh_token.
        Slaat het nieuwe token op in de Config-node. True bij succes."""
        s = self._servers.get(name) or {}
        refresh, client_id = s.get("refresh"), s.get("client_id")
        token_ep = s.get("token_endpoint")
        if not (refresh and client_id and token_ep):
            return False
        try:
            from span.integrations.mcp_oauth import refresh_token
            tok = refresh_token({"token_endpoint": token_ep}, client_id, refresh)
        except Exception as exc:
            print(f"[mcp] refresh '{name}' mislukt: {exc}", flush=True)
            return False
        s["token"] = tok.get("access_token", s.get("token"))
        if tok.get("refresh_token"):
            s["refresh"] = tok["refresh_token"]
        # persisteren zodat een herstart niet opnieuw hoeft te verversen
        if self._brain is not None:
            try:
                save_servers(self._brain, list(self._servers.values()))
            except Exception:
                pass
        if name in self._clients:
            self._clients[name].set_token(s["token"])
        return True

    def call(self, full_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # mcp__<server>__<tool>
        rest = full_name[len("mcp__"):]
        server, _, tool = rest.partition("__")
        client = self._clients.get(server)
        if client is None:
            return {"error": f"MCP-server '{server}' niet verbonden."}
        try:
            return client.call_tool(tool, arguments)
        except MCPError as exc:
            # token verlopen? ververs één keer en probeer opnieuw
            if str(exc) == "unauthorized" and self._try_refresh(server):
                try:
                    return client.call_tool(tool, arguments)
                except MCPError as exc2:
                    return {"error": f"MCP-fout (na refresh): {exc2}"}
            return {"error": f"MCP-fout: {exc}"}


def _parse_result(resp: requests.Response, expected_id: int | None = None) -> Any:
    """Haal het JSON-RPC-resultaat uit een directe JSON- of SSE-respons.

    Leest met een harde byte-cap (M6: geen geheugen-DoS bij een defecte/
    kwaadaardige server) en correleert de response-id met de verzonden id."""
    ctype = resp.headers.get("Content-Type", "")
    try:
        raw = resp.raw.read(MAX_RPC_BYTES + 1, decode_content=True) or b""
    except Exception:
        raw = resp.content[:MAX_RPC_BYTES + 1]
    finally:
        resp.close()
    if len(raw) > MAX_RPC_BYTES:
        raise MCPError(f"MCP-respons te groot (> {MAX_RPC_BYTES} bytes)")
    body = raw.decode(resp.encoding or "utf-8", errors="replace")
    if "text/event-stream" in ctype:
        # neem de laatste 'data:'-regel met een JSON-RPC response
        payload = None
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                    if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                        payload = obj
                except json.JSONDecodeError:
                    continue
        data = payload or {}
    else:
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError:
            raise MCPError("MCP-respons was geen geldige JSON")
    if isinstance(data, dict) and data.get("error"):
        raise MCPError(str(data["error"].get("message", data["error"])))
    # id-correlatie: een mismatch duidt op een verkeerd-gekoppelde/verwarde respons
    if (expected_id is not None and isinstance(data, dict)
            and data.get("id") is not None and data.get("id") != expected_id):
        raise MCPError(f"JSON-RPC id-mismatch (verwacht {expected_id}, kreeg {data.get('id')})")
    return data.get("result") if isinstance(data, dict) else None
