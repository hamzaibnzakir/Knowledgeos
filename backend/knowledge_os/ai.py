"""Provider-neutral AI gateway.

Only this module knows how to talk to a model provider. Everything else calls
`get_gateway().chat(...)` / `.embed(...)`. Every call is metered into the
`usage` table (tokens, latency, estimated cost, operation, actor).

Most providers expose an OpenAI-compatible Chat Completions API, so one
adapter covers OpenAI, OpenRouter, Ollama, Gemini, LM Studio, vLLM and any
custom compatible endpoint. A different protocol only needs a new class with
the same `chat`/`embed` methods.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import db
from .config import get_settings
from .util import KOSError, now

log = logging.getLogger("kos.ai")

PRESETS: dict[str, dict] = {
    "openai": {"base_url": "https://api.openai.com/v1", "tools": True, "json_mode": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "tools": True, "json_mode": True},
    "ollama": {"base_url": "http://localhost:11434/v1", "tools": True, "json_mode": True, "no_key": True},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "tools": True, "json_mode": True},
    "custom": {"base_url": "", "tools": True, "json_mode": False},
}

# USD per 1M tokens (input, output). Estimates only; override with KOS_PRICING_JSON.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw_message: dict = field(default_factory=dict)


class AIUnavailable(KOSError):
    status = 503


class Gateway:
    def __init__(self):
        s = get_settings()
        self.provider = s.ai_provider.lower()
        preset = PRESETS.get(self.provider, PRESETS["custom"])
        self.base_url = (s.ai_base_url or preset["base_url"]).rstrip("/")
        self.api_key = s.ai_api_key
        self.capabilities = {"tools": preset.get("tools", False), "json_mode": preset.get("json_mode", False)}
        self.no_key = preset.get("no_key", False)
        self.timeout = s.ai_timeout
        self._pricing = dict(DEFAULT_PRICING)
        if s.pricing_json:
            try:
                for k, v in json.loads(s.pricing_json).items():
                    self._pricing[k] = (float(v[0]), float(v[1]))
            except Exception:
                log.warning("Ignoring invalid KOS_PRICING_JSON")

    @property
    def configured(self) -> bool:
        return bool(self.base_url) and (bool(self.api_key) or self.no_key)

    def describe(self) -> dict:
        s = get_settings()
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "configured": self.configured,
            "chat_model": s.chat_model,
            "agent_model": s.resolved_agent_model,
            "utility_model": s.resolved_utility_model,
            "capabilities": self.capabilities,
        }

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _cost(self, model: str, inp: int, out: int) -> float | None:
        price = self._pricing.get(model)
        if price is None:
            for name, p in self._pricing.items():
                if model.startswith(name):
                    price = p
                    break
        if price is None:
            return None
        return round(inp / 1e6 * price[0] + out / 1e6 * price[1], 6)

    def _record(self, model: str, operation: str, inp: int, out: int, latency_ms: int, actor: str) -> None:
        try:
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO usage(provider,model,operation,input_tokens,output_tokens,created_at,cost_usd,latency_ms,actor)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (self.provider, model, operation, inp, out, now(), self._cost(model, inp, out), latency_ms, actor))
        except Exception as e:  # metering must never break a request
            log.warning("usage record failed: %s", e)

    def chat(self, messages: list[dict], *, model: str | None = None, tools: list[dict] | None = None,
             temperature: float | None = None, json_mode: bool = False, operation: str = "chat",
             actor: str = "web", max_tokens: int | None = None) -> ChatResult:
        if not self.configured:
            raise AIUnavailable("No AI provider is configured. Set KOS_AI_API_KEY (or OPENAI_API_KEY).")
        s = get_settings()
        model = model or s.chat_model
        body: dict[str, Any] = {"model": model, "messages": messages,
                                "temperature": s.ai_temperature if temperature is None else temperature}
        if tools and self.capabilities["tools"]:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode and self.capabilities["json_mode"]:
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_tokens"] = max_tokens
        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        except httpx.HTTPError as e:
            raise KOSError(f"AI provider request failed: {type(e).__name__}", status=502) from e
        latency = int((time.monotonic() - started) * 1000)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("error", {}).get("message", "")[:300]
            except Exception:
                pass
            self._record(model, operation + ".error", 0, 0, latency, actor)
            raise KOSError(f"AI provider returned {r.status_code}. {detail}".strip(), status=502)
        data = r.json()
        usage = data.get("usage") or {}
        inp, out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        self._record(data.get("model") or model, operation, inp, out, latency, actor)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        return ChatResult(content=msg.get("content") or "", tool_calls=msg.get("tool_calls") or [],
                          input_tokens=inp, output_tokens=out, model=data.get("model") or model, raw_message=msg)

    def embed(self, texts: list[str], *, model: str, operation: str = "embed", actor: str = "system") -> list[list[float]]:
        if not self.configured:
            raise AIUnavailable("No AI provider is configured for embeddings")
        started = time.monotonic()
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self.base_url}/embeddings", headers=self._headers(), json={"model": model, "input": texts})
        latency = int((time.monotonic() - started) * 1000)
        if r.status_code >= 400:
            raise KOSError(f"Embedding request failed with {r.status_code}", status=502)
        data = r.json()
        self._record(model, operation, int((data.get("usage") or {}).get("prompt_tokens") or 0), 0, latency, actor)
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def set_gateway(gw: Gateway | None) -> None:
    global _gateway
    _gateway = gw


def usage_summary(days: int = 30) -> dict:
    with db.connect() as conn:
        since = f"-{int(days)} days"
        totals = conn.execute(
            "SELECT count(*) calls, coalesce(sum(input_tokens),0) input_tokens, coalesce(sum(output_tokens),0) output_tokens,"
            " coalesce(sum(cost_usd),0) cost_usd FROM usage WHERE created_at >= datetime('now', ?)", (since,)).fetchone()
        by_model = conn.execute(
            "SELECT model, operation, count(*) calls, sum(input_tokens) input_tokens, sum(output_tokens) output_tokens,"
            " round(coalesce(sum(cost_usd),0),6) cost_usd, cast(avg(latency_ms) as int) avg_latency_ms FROM usage"
            " WHERE created_at >= datetime('now', ?) GROUP BY model, operation ORDER BY calls DESC", (since,)).fetchall()
        by_day = conn.execute(
            "SELECT substr(created_at,1,10) day, count(*) calls, sum(input_tokens)+sum(output_tokens) tokens,"
            " round(coalesce(sum(cost_usd),0),6) cost_usd FROM usage WHERE created_at >= datetime('now', ?)"
            " GROUP BY day ORDER BY day", (since,)).fetchall()
    return {"days": days, "totals": dict(totals), "by_model": [dict(r) for r in by_model], "by_day": [dict(r) for r in by_day]}
