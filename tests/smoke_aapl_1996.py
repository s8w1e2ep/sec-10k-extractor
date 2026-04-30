"""Live smoke test: AAPL FY 1996 plain-text 10-K end-to-end via /extract.

This is the Phase 2 counterpart to smoke_aapl.py — exercises the plain-text
path against a real pre-2002 EDGAR filing (file_url input form, since older
filings predate the structured Submissions metadata for primaryDocument).

Run with:
    SEC_CONTACT_EMAIL='you@example.com' .venv/bin/python tests/smoke_aapl_1996.py
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.pipeline import extract_filing


URL = "https://www.sec.gov/Archives/edgar/data/320193/0000320193-96-000023.txt"


async def main() -> int:
    if not os.environ.get("SEC_CONTACT_EMAIL"):
        print("ERROR: SEC_CONTACT_EMAIL env var required")
        return 2

    result = await extract_filing(file_url=URL)
    items = result["items"]
    stats = result["stats"]
    warnings = result["warnings"]

    print(f"Filing : {result['filing']['company_name']} ({result['filing']['form']})")
    print(f"  Filed : {result['filing']['filing_date']}  Period: {result['filing']['period_of_report']}")
    print(f"  URL   : {result['filing']['primary_document_url']}")
    print()
    print(f"Stats  : items_total={stats['items_total']}  located={len(items)}  missing={stats['items_missing']}")
    print(f"  by status  : extracted={stats['items_extracted']}  IBR={stats['items_incorporated_by_reference']}  N/A={stats['items_not_applicable']}  reserved={stats['items_reserved']}")
    print(f"  by strategy: {stats['strategies']}")
    print(f"  format={stats['format']}  duration={stats['duration_ms']}ms  fetch={stats['fetch_ms']}ms")
    print()

    print(f"{'#':<5}{'Part':<6}{'Title':<55}{'Status':<26}{'by':<10}{'len':>8}")
    print("-" * 110)
    for it in items:
        clen = it["char_range"]["end"] - it["char_range"]["start"]
        title = it["item_title"][:50]
        print(f"{it['item_number']:<5}{it['part']:<6}{title:<55}{it['status']:<26}{it['resolved_by']:<10}{clen:>8}")

    if warnings:
        print()
        for w in warnings:
            print(f"  warning: {w}")

    print()
    print("Phase 2 assertions:")
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    found_numbers = {it["item_number"] for it in items}
    pre_2003_required = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"}
    missing_required = pre_2003_required - found_numbers

    check("format detected as plain_text", stats["format"] == "plain_text")
    check("all 14 pre-2003 items located",
          not missing_required, f"missing={sorted(missing_required) or 'none'}")
    check("items_recall ≥ 80% (Phase 2 bar)",
          (len(items) / stats["items_total"]) >= 0.80 if stats["items_total"] else False,
          f"got {len(items)}/{stats['items_total']}")
    check("zero LLM calls (rules-only)", stats["llm_calls"] == 0)
    check("strategy is heading (no TOC anchors in plain text)",
          stats["strategies"]["heading"] > 0 and stats["strategies"]["toc"] == 0)
    # Phase 3 expectation: Item 14 era-renumber should fire as title_mismatch.
    title_mismatches = [w for w in warnings if w.get("code") == "title_mismatch"]
    item14_mismatch = any(w.get("item") == "14" for w in title_mismatches)
    check(
        "validator catches Item 14 era-rename (Phase 3 self-verification)",
        item14_mismatch,
        f"got {[w.get('item') for w in title_mismatches]} title mismatches",
    )

    if failures:
        print(f"\nFAILED: {len(failures)} assertion(s)")
        return 1
    print("\nAll Phase 2 assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
