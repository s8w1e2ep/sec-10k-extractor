"""Build eval/fixtures/filings.jsonl by probing the SEC Submissions API.

Lookup-only utility — finds the most recent 10-K (or 10-K/A) for a list of
hand-picked CIKs covering the eval categories from spec §5.1, and emits a
fixture line per company. After running this, hand-edit the resulting file
to add `expected_status_overrides` where you confidently know what the
filer wrote.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.fetcher import fetch


# (cik, label, [categories], form_filter)
CANDIDATES: list[tuple[str, str, list[str], str]] = [
    # modern_clean (also new_items_2023 if FY 2023+)
    ("320193", "Apple Inc.", ["modern_clean", "new_items_2023"], "10-K"),
    ("789019", "Microsoft Corp.", ["modern_clean", "new_items_2023"], "10-K"),
    ("1045810", "NVIDIA Corp.", ["modern_clean", "new_items_2023"], "10-K"),
    ("1318605", "Tesla Inc.", ["modern_clean", "new_items_2023"], "10-K"),
    # incorporation_heavy candidates
    ("1067983", "Berkshire Hathaway Inc.", ["incorporation_heavy"], "10-K"),
    ("104169", "Walmart Inc.", ["incorporation_heavy"], "10-K"),
    # bank
    ("19617", "JPMorgan Chase & Co.", ["bank"], "10-K"),
    # mining
    ("1164727", "Newmont Corp.", ["mining"], "10-K"),
    # 10-K/A amendment candidate (try several until we find one)
    ("320193", "Apple Inc. (most recent 10-K/A)", ["amendment"], "10-K/A"),
    # small_cap (placeholder — pick a known smaller filer)
    ("1411494", "Stitch Fix Inc.", ["small_cap"], "10-K"),
]


async def find_filing(cik: str, form: str = "10-K") -> dict | None:
    cik_padded = str(int(cik)).zfill(10)
    raw = await fetch(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    data = json.loads(raw)
    name = data.get("name") or data.get("entityName", "")
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    periods = recent.get("reportDate", [])
    primary = recent.get("primaryDocument", [])
    for i, f in enumerate(forms):
        if f == form:
            return {
                "company_name": name,
                "form": f,
                "accession_number": accs[i],
                "filing_date": dates[i],
                "period_of_report": periods[i] if i < len(periods) else None,
                "primary_document": primary[i],
            }
    return None


async def main() -> None:
    if not os.environ.get("SEC_CONTACT_EMAIL"):
        print("ERROR: SEC_CONTACT_EMAIL env var required")
        sys.exit(2)

    out_path = ROOT / "eval" / "fixtures" / "filings.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for cik, label, categories, form in CANDIDATES:
        try:
            info = await find_filing(cik, form=form)
        except Exception as e:
            print(f"  {label}: ERROR {e}")
            continue
        if not info:
            print(f"  {label}: no {form} found, skipping")
            continue
        record = {
            "cik": cik,
            "accession_number": info["accession_number"],
            "label": f"{info['company_name']} — {info['filing_date']} ({form})",
            "category": categories,
            "filing_date": info["filing_date"],
            "period_of_report": info["period_of_report"],
        }
        lines.append(json.dumps(record, ensure_ascii=False))
        print(f"  ✓ {label}: {info['accession_number']} ({info['filing_date']})")

    # Always include the AAPL 1996 plain-text fixture (legacy URL form)
    plain_text_fixture = {
        "cik": "320193",
        "accession_number": "0000320193-96-000023",
        "label": "Apple Computer Inc. — 1996-12-19 (10-K, plain text)",
        "category": ["plain_text"],
        "filing_date": "1996-12-19",
        "period_of_report": "1996-09-27",
        "file_url": "https://www.sec.gov/Archives/edgar/data/320193/0000320193-96-000023.txt",
    }
    lines.append(json.dumps(plain_text_fixture, ensure_ascii=False))
    print(f"  ✓ AAPL 1996 plain-text fixture (file_url path)")

    out_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {len(lines)} fixtures to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
