"""Anthropic API wrapper with cost tracking and hard caps.

Used by both the status resolver (Layer 1) and the locator fallback (Layer 2).
The pipeline enforces max 1 call per request — both layers share this client
and their results land in the same `stats.llm_calls` / `estimated_cost_usd`
counters.
"""

import json
import os
from dataclasses import dataclass

MODEL = "claude-haiku-4-5"
INPUT_PRICE_PER_MTOK = 1.0
OUTPUT_PRICE_PER_MTOK = 5.0
MAX_INPUT_CHARS = 50_000


class LLMNotConfigured(Exception):
    """Raised when ANTHROPIC_API_KEY is missing — caller falls back to rules-only."""


@dataclass
class LLMUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, in_tok: int, out_tok: int) -> None:
        self.calls += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.cost_usd += (
            in_tok * INPUT_PRICE_PER_MTOK / 1_000_000
            + out_tok * OUTPUT_PRICE_PER_MTOK / 1_000_000
        )


def get_api_key() -> str | None:
    """Return the credential to authenticate against api.anthropic.com.

    Checks `CLAUDE_CODE_OAUTH_TOKEN` first, then `ANTHROPIC_API_KEY`. Both go
    through the same `x-api-key` header — the public Messages API rejects
    OAuth tokens sent via `Authorization: Bearer`, so we treat the OAuth
    token as a regular API credential here.
    """
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get(
        "ANTHROPIC_API_KEY"
    )


def is_configured() -> bool:
    return bool(get_api_key())


async def call_json(
    *,
    system: str,
    user: str,
    usage: LLMUsage,
    max_output_tokens: int = 1024,
) -> dict:
    """Send a JSON-output prompt. Returns the parsed dict on success.

    Raises LLMNotConfigured when no API key. Raises ValueError when the
    response is not parseable JSON.
    """
    api_key = get_api_key()
    if not api_key:
        raise LLMNotConfigured(
            "Neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set"
        )

    if len(user) > MAX_INPUT_CHARS:
        user = user[:MAX_INPUT_CHARS]

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=MODEL,
        max_tokens=max_output_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    usage.add(response.usage.input_tokens, response.usage.output_tokens)

    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError(f"LLM did not return valid JSON: {text[:200]!r}")
