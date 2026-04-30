"""Tests for the abuse-prevention bundle (A: per-IP rate limit,
B: daily LLM cost ceiling, F: client_ip in structured log).

Each layer is exercised independently so a regression in one doesn't
mask another. State is in-process for all three, so every test resets
its slice via the test-only helpers (`_reset_rate_limiter_for_test`,
`_reset_daily_spend_for_test`).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from extractor import llm_client, pipeline
from extractor.llm_client import (
    LLMUsage,
    _daily_state,
    _record_daily_spend,
    _reset_daily_spend_for_test,
    daily_budget_remaining,
    daily_spent_usd,
)
from extractor.types import FilingMetadata


# ---------------------------------------------------------------------------
# A — per-IP rate limit
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    from server.main import _reset_rate_limiter_for_test

    _reset_rate_limiter_for_test()
    _reset_daily_spend_for_test()
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "test@example.com")
    yield
    _reset_rate_limiter_for_test()
    _reset_daily_spend_for_test()


def _client():
    from fastapi.testclient import TestClient
    from server.main import app

    return TestClient(app)


def test_rate_limit_allows_burst_then_blocks(monkeypatch):
    """Default config = 10/min burst 10. The 11th request from the same
    IP should get a 429, not a 200."""
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "10")
    monkeypatch.setenv("RATE_LIMIT_BURST", "10")
    client = _client()
    headers = {"X-Forwarded-For": "203.0.113.7"}

    # 10 calls fit in the burst.
    for i in range(10):
        r = client.post("/extract", json={"cik": "", "accession_number": ""}, headers=headers)
        # We expect 400 (input validation) — that's fine; we're proving
        # rate-limit middleware did NOT short-circuit before the handler.
        assert r.status_code != 429, f"rate-limited too early at i={i}"

    # 11th must be 429.
    r = client.post("/extract", json={"cik": "", "accession_number": ""}, headers=headers)
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "rate limit exceeded"
    assert body["limit_per_minute"] == 10
    assert r.headers.get("Retry-After") == "1"


def test_rate_limit_separates_by_xforwarded_for(monkeypatch):
    """Two distinct upstream IPs must NOT share a bucket. If the
    middleware treated both as ``request.client.host`` (the proxy IP),
    every Zeabur user would share a single 10/min bucket."""
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "5")
    monkeypatch.setenv("RATE_LIMIT_BURST", "5")
    client = _client()

    for _ in range(5):
        client.post("/extract", json={}, headers={"X-Forwarded-For": "10.0.0.1"})
    # IP A has now exhausted its bucket — but IP B still has a fresh one.
    r_a = client.post("/extract", json={}, headers={"X-Forwarded-For": "10.0.0.1"})
    r_b = client.post("/extract", json={}, headers={"X-Forwarded-For": "10.0.0.2"})
    assert r_a.status_code == 429
    assert r_b.status_code != 429


def test_rate_limit_exempts_healthz(monkeypatch):
    """Health probes must never be rate-limited; otherwise Zeabur or any
    monitor would flap the service into 429-loops."""
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    client = _client()
    headers = {"X-Forwarded-For": "10.0.0.99"}

    for _ in range(20):
        r = client.get("/healthz", headers=headers)
        assert r.status_code == 200


def test_rate_limit_falls_back_to_socket_when_no_xff():
    """Without an X-Forwarded-For header we still need a stable key.
    TestClient sets ``request.client.host = 'testclient'``."""
    client = _client()
    # Drain the bucket via the default identity (no XFF).
    for _ in range(10):
        client.post("/extract", json={})
    r = client.post("/extract", json={})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# B — daily LLM cost ceiling
# ---------------------------------------------------------------------------


def test_llm_usage_add_updates_daily_counter():
    """LLMUsage.add() must propagate cost into the process-wide counter,
    or B would silently never trip."""
    before = daily_spent_usd()
    u = LLMUsage()
    u.add(in_tok=1_000_000, out_tok=200_000)  # = $2.00 (per cost test)
    assert abs((daily_spent_usd() - before) - 2.0) < 1e-6


def test_daily_budget_resets_on_date_rollover(monkeypatch):
    """Yesterday's spend must not bleed into today's budget. We
    simulate a rollover by stamping the state with a past date."""
    monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "5.0")
    _record_daily_spend(4.99)
    assert daily_budget_remaining() < 0.02

    # Simulate the clock crossing midnight by pretending state was from yesterday.
    _daily_state["date"] = date.today() - timedelta(days=1)
    # Next read rolls the date forward and zeroes spend.
    assert abs(daily_budget_remaining() - 5.0) < 1e-6
    assert daily_spent_usd() == 0.0


def test_daily_budget_env_override(monkeypatch):
    monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.50")
    _reset_daily_spend_for_test()
    assert abs(daily_budget_remaining() - 0.50) < 1e-6
    _record_daily_spend(0.30)
    assert abs(daily_budget_remaining() - 0.20) < 1e-6


async def test_pipeline_skips_layer2_when_daily_budget_exhausted(monkeypatch, tmp_path):
    """When the ceiling is exhausted, Layer 2 must not call the LLM —
    pipeline still returns 200 with rules-only coverage and a warning."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "1.0")
    _reset_daily_spend_for_test()
    _record_daily_spend(2.0)  # already over budget
    assert daily_budget_remaining() < 0

    # Plain-text fixture — small, deterministic. Strip Item 14 to force
    # a missing required item and trigger the Layer 2 gate.
    fixture = tmp_path / "missing.txt"
    fixture.write_bytes(
        b"\f\nItem 1. Business\n\n" + b"Apple makes computers. " * 50 + b"\n\n"
    )
    raw = fixture.read_bytes()
    url = "https://www.sec.gov/Archives/edgar/data/320193/missing.txt"

    async def fake_resolve(cik, accession):
        return FilingMetadata(
            cik="0000320193", accession_number="0000320193-96-000023",
            form="10-K", filing_date="1996-12-19", period_of_report="1996-09-27",
            primary_document_url=url, company_name="Apple Computer Inc.",
        )

    async def fake_fetch(u, *, force=False):
        return raw

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    # Prove the LLM client is never reached.
    async def boom_call_json(**_):
        raise AssertionError("LLM must not be invoked when budget exhausted")

    from extractor import llm_resolver
    monkeypatch.setattr(llm_resolver, "call_json", boom_call_json)

    result = await pipeline.extract_filing(
        cik="320193", accession_number="0000320193-96-000023"
    )
    assert result["stats"]["llm_calls"] == 0
    codes = [w.get("code") for w in result["warnings"]]
    assert "llm_skipped_daily_budget_exhausted" in codes


# ---------------------------------------------------------------------------
# F — client_ip in structured http.request log
# ---------------------------------------------------------------------------


def test_http_request_log_includes_client_ip(caplog):
    client = _client()
    with caplog.at_level(logging.INFO, logger="server"):
        r = client.get("/healthz", headers={"X-Forwarded-For": "198.51.100.42"})
    assert r.status_code == 200

    http_logs = [r for r in caplog.records if getattr(r, "event", None) == "http.request"]
    assert http_logs, "no http.request log emitted"
    assert http_logs[-1].client_ip == "198.51.100.42"


def test_http_request_log_strips_xff_chain(caplog):
    """X-Forwarded-For can contain a comma-separated chain when traffic
    passes through multiple proxies. We log the FIRST entry (the
    original client), not the whole string."""
    client = _client()
    with caplog.at_level(logging.INFO, logger="server"):
        client.get(
            "/healthz",
            headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1, 10.0.0.2"},
        )
    http_logs = [r for r in caplog.records if getattr(r, "event", None) == "http.request"]
    assert http_logs[-1].client_ip == "198.51.100.7"


def test_http_request_log_records_429_for_rate_limited_calls(caplog, monkeypatch):
    """Even when the rate limiter short-circuits, the outer logging
    middleware must still record the request — that's what makes F a
    useful forensic signal (otherwise abuse goes invisible)."""
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    client = _client()
    headers = {"X-Forwarded-For": "203.0.113.99"}

    with caplog.at_level(logging.INFO, logger="server"):
        client.post("/extract", json={}, headers=headers)
        r = client.post("/extract", json={}, headers=headers)
    assert r.status_code == 429

    http_logs = [
        rec for rec in caplog.records
        if getattr(rec, "event", None) == "http.request"
        and getattr(rec, "client_ip", None) == "203.0.113.99"
    ]
    assert any(rec.status == 429 for rec in http_logs)
