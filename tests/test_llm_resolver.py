"""Tests for the LLM-backed resolvers (Layer 1 status, Layer 2 locator).

We never call the real Anthropic API in tests — `extractor.llm_client.call_json`
is monkeypatched to return canned dicts. The cap (1 call/request) is enforced
by pipeline glue, not the resolvers themselves; here we verify each layer's
contract in isolation.
"""

import pytest

from extractor import llm_resolver
from extractor.llm_client import LLMNotConfigured, LLMUsage
from extractor.types import ExtractedItem, NormalizedDoc


def _item(num, *, status="extracted", text="some content", start=0, end=1000):
    return ExtractedItem(
        part="II", item_number=num, item_title="title",
        content_text=text,
        char_range_start=start, char_range_end=end,
        status=status, resolved_by="toc",
    )


@pytest.mark.asyncio
async def test_resolve_statuses_corrects_status(monkeypatch):
    async def fake_call(**_):
        return {
            "decisions": [
                {
                    "item_number": "14",
                    "status": "incorporated_by_reference",
                    "reason": "Section directs reader to proxy statement",
                }
            ]
        }

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    items = [_item("14", status="extracted")]
    warns = [{"code": "title_mismatch", "item": "14"}]
    usage = LLMUsage()

    updated, new_warns = await llm_resolver.resolve_statuses(items, warns, usage=usage)

    assert updated[0].status == "incorporated_by_reference"
    assert any(w["code"] == "status_corrected_by_llm" for w in new_warns)
    assert new_warns[0]["old_status"] == "extracted"
    assert new_warns[0]["new_status"] == "incorporated_by_reference"


@pytest.mark.asyncio
async def test_resolve_statuses_keeps_status_when_llm_agrees(monkeypatch):
    async def fake_call(**_):
        return {
            "decisions": [
                {"item_number": "14", "status": "extracted", "reason": "real content"}
            ]
        }

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    items = [_item("14", status="extracted")]
    warns = [{"code": "title_mismatch", "item": "14"}]
    usage = LLMUsage()

    updated, new_warns = await llm_resolver.resolve_statuses(items, warns, usage=usage)
    assert updated[0].status == "extracted"
    assert new_warns == []


@pytest.mark.asyncio
async def test_resolve_statuses_skips_when_no_flagged_items(monkeypatch):
    called = []

    async def fake_call(**kwargs):
        called.append(kwargs)
        return {}

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    items = [_item("14")]
    usage = LLMUsage()
    updated, new_warns = await llm_resolver.resolve_statuses(items, [], usage=usage)

    assert updated == items
    assert new_warns == []
    assert called == []
    assert usage.calls == 0


@pytest.mark.asyncio
async def test_resolve_statuses_handles_no_api_key(monkeypatch):
    async def fake_call(**_):
        raise LLMNotConfigured("test")

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    items = [_item("14")]
    warns = [{"code": "title_mismatch", "item": "14"}]
    usage = LLMUsage()
    updated, new_warns = await llm_resolver.resolve_statuses(items, warns, usage=usage)

    assert updated == items
    assert any(w["code"] == "llm_skipped_no_api_key" for w in new_warns)


@pytest.mark.asyncio
async def test_resolve_statuses_ignores_invalid_status_value(monkeypatch):
    async def fake_call(**_):
        return {
            "decisions": [
                {"item_number": "14", "status": "garbage", "reason": "..."},
            ]
        }

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    items = [_item("14", status="extracted")]
    warns = [{"code": "title_mismatch", "item": "14"}]
    usage = LLMUsage()
    updated, new_warns = await llm_resolver.resolve_statuses(items, warns, usage=usage)
    # garbage status ignored — original kept
    assert updated[0].status == "extracted"
    assert new_warns == []


def _doc(text):
    return NormalizedDoc(text=text, headings=[], anchors=[], format="html_modern")


@pytest.mark.asyncio
async def test_fallback_locator_finds_missing_item(monkeypatch):
    text = (
        "PART I\n\nItem 1. Business\n\n"
        + "Business content " * 100
        + "\n\nItem 1A. Risk Factors\n\n"
        + "Risk content " * 100
    )
    snippet = "Item 1A. Risk Factors\n\nRisk content"

    async def fake_call(**_):
        return {
            "found_items": [
                {"item_number": "1A", "start_snippet": snippet}
            ]
        }

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    located = [_item("1", start=text.find("Item 1. Business"), end=len(text))]
    usage = LLMUsage()

    spans, warns = await llm_resolver.fallback_locator(
        _doc(text), located, missing_numbers=["1A"], usage=usage
    )

    assert len(spans) == 1
    assert spans[0].item_number == "1A"
    assert spans[0].resolved_by == "llm"
    assert spans[0].start == text.find("Item 1A. Risk Factors")


@pytest.mark.asyncio
async def test_fallback_locator_skips_when_snippet_missing(monkeypatch):
    text = "PART I\nItem 1. Business\n\n" + "Content " * 100

    async def fake_call(**_):
        return {
            "found_items": [
                {"item_number": "1A", "start_snippet": "completely fictional snippet that's not in the text"}
            ]
        }

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    usage = LLMUsage()
    spans, warns = await llm_resolver.fallback_locator(
        _doc(text), [], missing_numbers=["1A"], usage=usage
    )

    assert spans == []
    assert any(w["code"] == "llm_locator_partial" for w in warns)


@pytest.mark.asyncio
async def test_fallback_locator_no_op_when_nothing_missing():
    usage = LLMUsage()
    spans, warns = await llm_resolver.fallback_locator(
        _doc("anything"), [], missing_numbers=[], usage=usage
    )
    assert spans == []
    assert warns == []
    assert usage.calls == 0


@pytest.mark.asyncio
async def test_fallback_locator_handles_no_api_key(monkeypatch):
    async def fake_call(**_):
        raise LLMNotConfigured("test")

    monkeypatch.setattr(llm_resolver, "call_json", fake_call)

    usage = LLMUsage()
    spans, warns = await llm_resolver.fallback_locator(
        _doc("text"), [], missing_numbers=["1A"], usage=usage
    )
    assert spans == []
    assert any(w["code"] == "llm_skipped_no_api_key" for w in warns)


def test_llm_usage_cost_math():
    """Sanity-check the price formula. Haiku 4.5 = $1/MTok in, $5/MTok out."""
    u = LLMUsage()
    u.add(in_tok=1_000_000, out_tok=200_000)
    assert u.calls == 1
    assert u.input_tokens == 1_000_000
    assert u.output_tokens == 200_000
    # 1 * $1 + 0.2 * $5 = $2.00
    assert abs(u.cost_usd - 2.0) < 1e-6
