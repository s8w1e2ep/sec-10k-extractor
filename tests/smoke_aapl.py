"""Live smoke test: extract AAPL's most recent 10-K end-to-end.

Run with:
    SEC_CONTACT_EMAIL='you@example.com' .venv/bin/python tests/smoke_aapl.py

Resolves AAPL's most recent 10-K via Submissions API (no hardcoded accession),
runs the full pipeline, and prints a per-item summary + assertions.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.fetcher import fetch
from extractor.pipeline import extract_filing


AAPL_CIK = "320193"


async def find_most_recent_10k(cik: str) -> str:
    """Return the accession number of the company's most recent 10-K filing."""
    raw = await fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    data = json.loads(raw)
    recent = data["filings"]["recent"]
    forms = recent["form"]
    accs = recent["accessionNumber"]
    for form, acc in zip(forms, accs):
        if form == "10-K":
            return acc
    raise RuntimeError(f"No 10-K found for CIK {cik}")


async def main() -> int:
    if not os.environ.get("SEC_CONTACT_EMAIL"):
        print("ERROR: SEC_CONTACT_EMAIL env var required")
        return 2

    print(f"Resolving most recent AAPL 10-K (CIK {AAPL_CIK})...")
    accession = await find_most_recent_10k(AAPL_CIK)
    print(f"  → accession = {accession}")

    print("Running pipeline...")
    result = await extract_filing(cik=AAPL_CIK, accession_number=accession)

    filing = result["filing"]
    items = result["items"]
    stats = result["stats"]
    warnings = result["warnings"]

    print()
    print(f"Filing : {filing['company_name']} ({filing['form']})")
    print(f"  CIK     : {filing['cik']}")
    print(f"  Accession: {filing['accession_number']}")
    print(f"  Filed   : {filing['filing_date']}")
    print(f"  Period  : {filing['period_of_report']}")
    print(f"  URL     : {filing['primary_document_url']}")
    print()
    print(f"Stats  : items_total={stats['items_total']}  items_missing={stats['items_missing']}")
    print(f"  by status   : extracted={stats['items_extracted']}  IBR={stats['items_incorporated_by_reference']}  N/A={stats['items_not_applicable']}  reserved={stats['items_reserved']}")
    print(f"  by strategy : {stats['strategies']}")
    print(f"  format={stats['format']}  duration={stats['duration_ms']}ms  fetch={stats['fetch_ms']}ms")
    print()
    print(f"{'#':<5}{'Part':<6}{'Title':<60}{'Status':<26}{'len':>8}")
    print("-" * 110)
    for it in items:
        title = it["item_title"][:55]
        clen = it["char_range"]["end"] - it["char_range"]["start"]
        print(f"{it['item_number']:<5}{it['part']:<6}{title:<60}{it['status']:<26}{clen:>8}")

    if warnings:
        print()
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")

    # Acceptance assertions for Phase 1
    print()
    print("Phase 1 assertions:")
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    item_numbers = {it["item_number"] for it in items}
    item_status = {it["item_number"]: it["status"] for it in items}

    check("at least 18 items located", len(items) >= 18, f"got {len(items)}")
    check("Item 6 == reserved", item_status.get("6") == "reserved", f"got {item_status.get('6')}")
    check("items_missing == 0", stats["items_missing"] == 0, f"got {stats['items_missing']}")
    check("Item 1 located", "1" in item_numbers)
    check("Item 1A located", "1A" in item_numbers)
    check("Item 7 located", "7" in item_numbers)
    check("Item 8 located", "8" in item_numbers)
    check("at least one item is incorporated_by_reference",
          any(it["status"] == "incorporated_by_reference" for it in items))
    check("zero LLM calls (Phase 1 is rules-only)", stats["llm_calls"] == 0)
    check(
        "no validator warnings on clean modern filing",
        not warnings,
        f"got {len(warnings)} warning(s): {[w.get('code') for w in warnings]}",
    )

    if failures:
        print(f"\nFAILED: {len(failures)} assertion(s)")
        return 1
    print("\nAll Phase 1 assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
