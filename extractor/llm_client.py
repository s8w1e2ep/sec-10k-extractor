"""Anthropic API wrapper with cost tracking and hard caps.

Used by both the status resolver (Layer 1) and the locator fallback (Layer 2).
The pipeline enforces a shared per-request budget of
``MAX_LLM_CALLS_PER_REQUEST`` calls — both layers count against the same
``LLMUsage`` instance and surface in ``stats.llm_calls`` /
``estimated_cost_usd``.

Two credential paths supported:

- `ANTHROPIC_API_KEY` (regular API key, `sk-ant-api...`) — sent as
  `x-api-key` header.
- `CLAUDE_CODE_OAUTH_TOKEN` (long-lived OAuth token issued by
  `claude setup-token`, `sk-ant-oat...`) — sent as `Authorization: Bearer`
  with the `anthropic-beta: oauth-2025-04-20` header. Requests
  authenticated with an OAuth token must declare themselves as Claude
  Code in the system prompt or the API rejects them; we prepend the
  required identifier line transparently.
"""

import json
import os
from dataclasses import dataclass
from datetime import date

MODEL = "claude-haiku-4-5"
INPUT_PRICE_PER_MTOK = 1.0
OUTPUT_PRICE_PER_MTOK = 5.0
MAX_INPUT_CHARS = 50_000

# Shared per-request budget. Both Layer 1 (status resolver) and
# Layer 2 (locator fallback) consume from this — see pipeline.py.
# Today each layer makes one call max, so the realistic ceiling is 2;
# the headroom to 3 leaves room for a future Layer 2 retry / chunked
# pass without a new architectural change.
MAX_LLM_CALLS_PER_REQUEST = 3


def _daily_budget_usd() -> float:
    """Read on every check so tests can monkeypatch the env var."""
    try:
        return float(os.environ.get("DAILY_LLM_BUDGET_USD", "5.0"))
    except ValueError:
        return 5.0


# Process-wide daily LLM spend ceiling. Resets on UTC date rollover.
# Pipeline gates check `daily_budget_remaining() > 0` before invoking
# either LLM layer. Once exhausted, requests still succeed via rules
# only and emit a `llm_skipped_daily_budget_exhausted` warning.
#
# Lives in this module (not pipeline) so the counter is updated
# atomically inside ``LLMUsage.add()`` whenever a call lands, regardless
# of which layer made it.
_daily_state: dict = {"date": date.today(), "spent_usd": 0.0}


def _roll_daily_state() -> None:
    today = date.today()
    if _daily_state["date"] != today:
        _daily_state["date"] = today
        _daily_state["spent_usd"] = 0.0


def daily_budget_remaining() -> float:
    _roll_daily_state()
    return _daily_budget_usd() - _daily_state["spent_usd"]


def daily_spent_usd() -> float:
    _roll_daily_state()
    return _daily_state["spent_usd"]


def _record_daily_spend(cost_usd: float) -> None:
    _roll_daily_state()
    _daily_state["spent_usd"] += cost_usd


def _reset_daily_spend_for_test() -> None:
    """Test-only hook. Resets the process-wide counter."""
    _daily_state["date"] = date.today()
    _daily_state["spent_usd"] = 0.0

OAUTH_BETA_HEADER = "oauth-2025-04-20"
CLAUDE_CODE_IDENTIFIER = "You are Claude Code, Anthropic's official CLI for Claude."


class LLMNotConfigured(Exception):
    """Raised when no LLM credential is set — caller falls back to rules-only."""


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
        delta = (
            in_tok * INPUT_PRICE_PER_MTOK / 1_000_000
            + out_tok * OUTPUT_PRICE_PER_MTOK / 1_000_000
        )
        self.cost_usd += delta
        _record_daily_spend(delta)


def _oauth_token() -> str | None:
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def get_api_key() -> str | None:
    """Return whichever credential is available (OAuth token preferred)."""
    return _oauth_token() or _api_key()


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

    Raises LLMNotConfigured when no credential. Raises ValueError when the
    response is not parseable JSON.
    """
    if len(user) > MAX_INPUT_CHARS:
        user = user[:MAX_INPUT_CHARS]

    oauth = _oauth_token()
    api_key = _api_key()

    from anthropic import AsyncAnthropic

    if oauth:
        client = AsyncAnthropic(
            auth_token=oauth,
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )
        # OAuth-authenticated requests must declare themselves as Claude
        # Code or the API returns 401. Prepend the identifier; our actual
        # task instructions follow as a separate paragraph.
        system_payload = f"{CLAUDE_CODE_IDENTIFIER}\n\n{system}"
    elif api_key:
        client = AsyncAnthropic(api_key=api_key)
        system_payload = system
    else:
        raise LLMNotConfigured(
            "Neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set"
        )

    response = await client.messages.create(
        model=MODEL,
        max_tokens=max_output_tokens,
        system=system_payload,
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
