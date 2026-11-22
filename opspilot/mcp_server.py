"""
Lightweight MCP (Model Context Protocol) JSON-RPC 2.0 stdio server exposing OpsPilot's
4 read-only investigation tools (search_logs, search_docs, get_context, log_stats).

Allows MCP-compatible clients (Claude Desktop, Cursor, Antigravity) to query OpsPilot directly.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .agent import Agent
from .tool_agent import REGISTRY

MCP_TOOLS = ["search_logs", "search_docs", "get_context", "log_stats"]


class OpsPilotMCPServer:
    def __init__(self, agent: Agent | None = None) -> None:
        self.agent = agent or Agent()

    def list_tools(self) -> list[dict[str, Any]]:
        tools = []
        for name in MCP_TOOLS:
            schema_cls, desc = REGISTRY[name]
            tools.append(
                {
                    "name": name,
                    "description": desc,
                    "inputSchema": schema_cls.model_json_schema(),
                }
            )
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in MCP_TOOLS:
            raise ValueError(f"Unknown MCP tool: {name}")
        schema_cls = REGISTRY[name][0]
        args = schema_cls.model_validate(arguments)

        if name == "search_logs":
            data = self.agent.logs.search(args.query, "hybrid", args.k)
        elif name == "search_docs":
            data = self.agent.docs.search(args.query, "hybrid", min(args.k, 5))
        elif name == "get_context":
            data = self.agent.tools.context(args.line, args.radius)
        else:
            data = self.agent.tools.filtered_stats(args.contains, args.level, args.start, args.end)
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "opspilot-mcp", "version": "0.3.0"},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                result = self.call_tool(params.get("name", ""), params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}


def main() -> None:
    server = OpsPilotMCPServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = server.handle_request(json.loads(line))
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
