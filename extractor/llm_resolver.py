"""LLM-backed resolvers — fenced last resort behind rules-based extraction.

Two layers, sharing the per-request 1-call cap:

- **Layer 1 (status_resolver)**: when `validator` flags `title_mismatch` on
  one or more `extracted` items, ask the LLM to confirm/correct status.
  Catches era-renames (pre-2003 Item 14 == today's Item 15) and unusual
  IBR phrasings (Berkshire) that pure rules miss.

- **Layer 2 (fallback_locator)**: when the rules locator did not find
  every required item for the filing's period, ask the LLM to find them
  in the first 50KB of the normalized document.

When both layers want to fire, Layer 2 wins (missing items > wrong status).
This keeps total LLM cost at ≤ 1 call per request.
"""

import json
from dataclasses import replace
from pathlib import Path

from .canonical_items import get_canonical_item, part_sort_key
from .llm_client import LLMNotConfigured, LLMUsage, call_json
from .types import ExtractedItem, ItemSpan, NormalizedDoc

PROMPTS_DIR = Path(__file__).parent / "prompts"
STATUS_VALUES = {
    "extracted",
    "incorporated_by_reference",
    "not_applicable",
    "reserved",
}


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


async def resolve_statuses(
    items: list[ExtractedItem],
    title_mismatch_warnings: list[dict],
    *,
    usage: LLMUsage,
) -> tuple[list[ExtractedItem], list[dict]]:
    """Layer 1: confirm/correct status for items flagged by `title_mismatch`.

    Returns `(updated_items, new_warnings)`. Original `items` not mutated.
    """
    flagged_nums = {
        w["item"] for w in title_mismatch_warnings if w.get("item")
    }
    if not flagged_nums:
        return items, []

    flagged_items = [it for it in items if it.item_number in flagged_nums]
    if not flagged_items:
        return items, []

    payload = {
        "items": [
            {
                "item_number": it.item_number,
                "canonical_title": (
                    get_canonical_item(it.item_number).title
                    if get_canonical_item(it.item_number)
                    else ""
                ),
                "current_status": it.status,
                "section_text": it.content_text[:2000],
            }
            for it in flagged_items
        ],
    }

    try:
        result = await call_json(
            system=_load_prompt("status_resolver.md"),
            user=json.dumps(payload, indent=2),
            usage=usage,
            max_output_tokens=1024,
        )
    except LLMNotConfigured:
        return items, [{
            "code": "llm_skipped_no_api_key",
            "message": (
                "Status resolver would have run but ANTHROPIC_API_KEY is unset"
            ),
        }]
    except Exception as e:
        return items, [{
            "code": "llm_error",
            "message": f"Status resolver failed: {e}",
        }]

    decisions = {
        d["item_number"]: d
        for d in result.get("decisions", [])
        if isinstance(d, dict) and "item_number" in d
    }

    updated = list(items)
    new_warnings: list[dict] = []
    for i, it in enumerate(updated):
        d = decisions.get(it.item_number)
        if not d:
            continue
        new_status = d.get("status")
        if new_status not in STATUS_VALUES:
            continue
        if new_status != it.status:
            updated[i] = replace(it, status=new_status)
            new_warnings.append({
                "code": "status_corrected_by_llm",
                "message": (
                    f"Item {it.item_number}: {it.status} → {new_status} "
                    f"({d.get('reason', 'no reason given')})"
                ),
                "item": it.item_number,
                "old_status": it.status,
                "new_status": new_status,
            })

    return updated, new_warnings


async def fallback_locator(
    doc: NormalizedDoc,
    located_items: list[ExtractedItem],
    missing_numbers: list[str],
    *,
    usage: LLMUsage,
) -> tuple[list[ItemSpan], list[dict]]:
    """Layer 2: find required items the rules locator missed.

    Returns `(additional_spans, warnings)`. Caller is responsible for
    re-sorting and recomputing `end` offsets after merging.
    """
    if not missing_numbers:
        return [], []

    located_summary = [
        {"item_number": it.item_number, "start": it.char_range_start}
        for it in sorted(located_items, key=lambda x: x.char_range_start)
    ]
    payload = {
        "missing_item_numbers": sorted(
            missing_numbers, key=lambda n: part_sort_key("I", n)
        ),
        "located_so_far": located_summary,
        "document_snippet": doc.text[:50_000],
    }

    try:
        result = await call_json(
            system=_load_prompt("locator_fallback.md"),
            user=json.dumps(payload, indent=2),
            usage=usage,
            max_output_tokens=1024,
        )
    except LLMNotConfigured:
        return [], [{
            "code": "llm_skipped_no_api_key",
            "message": (
                "Locator fallback would have run but ANTHROPIC_API_KEY is unset"
            ),
        }]
    except Exception as e:
        return [], [{
            "code": "llm_error",
            "message": f"Locator fallback failed: {e}",
        }]

    located_set = {it.item_number for it in located_items}
    spans: list[ItemSpan] = []
    skipped: list[str] = []
    for entry in result.get("found_items", []):
        if not isinstance(entry, dict):
            continue
        num = entry.get("item_number")
        snippet = entry.get("start_snippet", "")
        if not num or not isinstance(snippet, str) or len(snippet) < 10:
            continue
        if num in located_set:
            continue
        canonical = get_canonical_item(num)
        if not canonical:
            skipped.append(num)
            continue
        # Try the full snippet first; fall back to first 60 chars.
        offset = doc.text.find(snippet)
        if offset == -1:
            head = snippet[:60]
            if len(head) >= 20:
                offset = doc.text.find(head)
        if offset == -1:
            skipped.append(num)
            continue
        spans.append(ItemSpan(
            part=canonical.part,
            item_number=canonical.item_number,
            item_title=canonical.title,
            start=offset,
            end=len(doc.text),  # caller recomputes
            resolved_by="llm",
        ))

    warnings: list[dict] = []
    if skipped:
        warnings.append({
            "code": "llm_locator_partial",
            "message": (
                f"LLM proposed items but their snippets did not match the "
                f"document (or item number was unknown): {sorted(set(skipped))}"
            ),
            "items": sorted(set(skipped)),
        })
    return spans, warnings
