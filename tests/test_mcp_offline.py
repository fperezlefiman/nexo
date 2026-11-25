"""MCP protocol tests: framing, discovery and error paths that need no database."""

import json

from nexo.mcp import TOOLS, handle


def test_initialize_returns_capabilities():
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == "nexo"


def test_notification_gets_no_answer():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_exposes_every_handler():
    response = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [tool["name"] for tool in TOOLS]
    assert {"memory_write", "session_search", "job_create", "skill_write"} <= set(names)


def test_every_tool_has_a_schema_and_a_description():
    for tool in TOOLS:
        assert tool["description"].strip().endswith(".")
        assert tool["inputSchema"]["type"] == "object"


def test_unknown_method_is_an_error_not_a_crash():
    response = handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert response["error"]["code"] == -32601


def test_unknown_tool_is_an_error():
    response = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}})
    assert response["error"]["code"] == -32602


def test_response_is_json_serializable():
    response = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    assert json.loads(json.dumps(response))["id"] == 5


def test_memory_search_falls_back_to_words_when_the_vector_is_missing(monkeypatch):
    """A profile with memories but no embeddings returned "no matches" instead of searching text."""
    from datetime import datetime, timezone

    from nexo import mcp

    monkeypatch.setattr(mcp, "_profile_id", lambda _name: "prf")
    monkeypatch.setattr(mcp, "_vector_search", lambda *a, **k: [])
    monkeypatch.setattr(mcp, "_lexical_search", lambda *a, **k: [
        {"content": "el alquiler vence el 5", "kind": "fact", "created_at": datetime.now(timezone.utc)}
    ])
    result = mcp.memory_search({"profile": "finance", "query": "alquiler"})
    assert "alquiler" in result["content"][0]["text"]
