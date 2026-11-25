import json

from knowledge_os import agent, ai, memory, tools, vault
from knowledge_os.ai import ChatResult


class ScriptedGateway(ai.Gateway):
    """Plays back a fixed sequence of model responses and records what it was sent."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.sent = []
        self.api_key = "test"

    @property
    def configured(self):
        return True

    def chat(self, messages, **kw):
        self.sent.append(list(messages))
        self._record(kw.get("model") or "gpt-4o-mini", kw.get("operation", "chat"), 100, 20, 5, "test")
        return self.script.pop(0)


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_agent_creates_and_links_note(env):
    vault.create("Notes/Creative Testing.md", "# Creative Testing\nhooks and angles", "t")
    gw = ScriptedGateway([
        ChatResult(content="", tool_calls=[call("search_notes", {"query": "creative testing"})]),
        ChatResult(content="", tool_calls=[call("create_note", {"path": "Notes/Meta Ads Testing",
                                                               "content": "# Meta Ads Testing\nSee [[Creative Testing]]"})]),
        ChatResult(content="Created [[Meta Ads Testing]] and linked it to [[Creative Testing]] [1]."),
    ])
    ai.set_gateway(gw)
    chat = agent.create_chat()
    out = agent.send_message(chat["id"], "Create a note about Meta Ads testing linked to my creative testing notes")
    assert vault.exists("Notes/Meta Ads Testing.md")
    assert [a["tool"] for a in out["meta"]["actions"]] == ["search_notes", "create_note"]
    assert out["meta"]["usage"]["calls"] == 3
    # context was retrieved and sent, not the whole vault
    first_user = gw.sent[0][-1]["content"]
    assert "<context>" in first_user and "Creative Testing" in first_user
    from knowledge_os import indexer
    assert indexer.backlinks("Notes/Creative Testing.md") == ["Notes/Meta Ads Testing.md"]
    assert ai.usage_summary()["totals"]["calls"] == 3


def test_destructive_action_needs_approval(env):
    vault.create("Old.md", "obsolete", "t")
    ai.set_gateway(ScriptedGateway([
        ChatResult(content="", tool_calls=[call("delete_note", {"path": "Old.md"})]),
        ChatResult(content="Waiting for your approval to delete it."),
    ]))
    chat = agent.create_chat()
    out = agent.send_message(chat["id"], "delete Old")
    assert vault.exists("Old.md")
    action = out["meta"]["actions"][0]
    assert action["status"] == "needs_confirmation"
    tools.resolve_pending(action["pending_id"], approve=True)
    assert not vault.exists("Old.md")


def test_tool_errors_reported_to_model(env):
    gw = ScriptedGateway([
        ChatResult(content="", tool_calls=[call("read_note", {"path": "../secret"})]),
        ChatResult(content="I could not read that."),
    ])
    ai.set_gateway(gw)
    chat = agent.create_chat()
    out = agent.send_message(chat["id"], "read it")
    assert out["meta"]["actions"][0]["status"] == "failed"
    assert "error" in gw.sent[1][-1]["content"]


def test_offline_answer_without_ai(env):
    vault.create("Notes/Pricing.md", "We price at cost plus 40 percent", "t")
    chat = agent.create_chat()
    out = agent.send_message(chat["id"], "how do we price")
    assert "not configured" in out["content"] and "Pricing" in out["content"]


def test_scope_enforced(env):
    import pytest
    from knowledge_os.util import Forbidden
    ctx = tools.ToolContext(actor="mcp:reader", scopes={tools.READ})
    with pytest.raises(Forbidden):
        tools.call("create_note", {"path": "x", "content": "y"}, ctx)
    assert tools.call("list_notes", {}, ctx) == []


def test_memory_extraction_returns_candidates_only(env):
    ai.set_gateway(ScriptedGateway([ChatResult(content=json.dumps({"memories": [
        {"content": "Uses an iPhone", "category": "profile", "confidence": 0.9}]}))]))
    chat = agent.create_chat()
    from knowledge_os import db
    with db.connect() as conn:
        conn.execute("INSERT INTO messages(chat_id,role,content,created_at,meta) VALUES(?,?,?,?,?)",
                     (chat["id"], "user", "I use an iPhone", "now", "{}"))
    cands = memory.extract_candidates(chat["id"])
    assert cands[0]["content"] == "Uses an iPhone"
    assert memory.list_memories() == []
