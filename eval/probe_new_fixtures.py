"""One-off probe: find specific 10-K accessions for the new fixtures.

Most recent 10-K for the four candidates Claude proposed (Kura Sushi /
Moderna / Caterpillar / Chipotle), plus older-year picks the user
specified (Tiffany FY 2015, Disney FY 2002).

Older filings live in `data['filings']['files']` (older quarterly chunks)
not `data['filings']['recent']`. We walk both. Output is JSONL lines
ready to paste into eval/fixtures/filings.jsonl.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.fetcher import fetch


# (cik, label_prefix, categories, target_year | None for "most recent")
TARGETS: list[tuple[str, str, list[str], int | None]] = [
    ("1772177", "Kura Sushi USA", ["small_cap", "restaurant"], None),
    ("1682852", "Moderna Inc.", ["biotech"], None),
    ("18230",   "Caterpillar Inc.", ["industrial"], None),
    ("1058090", "Chipotle Mexican Grill", ["restaurant"], None),
    ("98246",   "Tiffany & Co.", ["luxury_retail"], 2015),
    ("1001039", "Walt Disney Co. (legacy)", ["entertainment", "pre_sox"], 2002),
]


async def fetch_all_filings(cik: str) -> tuple[str, list[dict]]:
    cik_padded = str(int(cik)).zfill(10)
    raw = await fetch(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    data = json.loads(raw)
    name = data.get("name") or data.get("entityName", "")

    out: list[dict] = []

    def _ingest(arrs: dict) -> None:
        forms = arrs.get("form", [])
        accs = arrs.get("accessionNumber", [])
        dates = arrs.get("filingDate", [])
        periods = arrs.get("reportDate", [])
        primary = arrs.get("primaryDocument", [])
        for i, f in enumerate(forms):
            out.append({
                "form": f,
                "accession": accs[i],
                "filing_date": dates[i] if i < len(dates) else "",
                "period_of_report": periods[i] if i < len(periods) else None,
                "primary_doc": primary[i] if i < len(primary) else "",
            })

    _ingest(data.get("filings", {}).get("recent", {}))
    for f in data.get("filings", {}).get("files", []):
        chunk_url = f"https://data.sec.gov/submissions/{f['name']}"
        chunk_raw = await fetch(chunk_url)
        chunk = json.loads(chunk_raw)
        _ingest(chunk)

    return name, out


def _is_10k_family(form: str) -> bool:
    f = (form or "").upper()
    return f.startswith("10-K") and "/A" not in f


async def main() -> None:
    if not os.environ.get("SEC_CONTACT_EMAIL"):
        print("ERROR: SEC_CONTACT_EMAIL env var required")
        sys.exit(2)

    new_lines: list[str] = []
    for cik, label_prefix, categories, target_year in TARGETS:
        try:
            name, filings = await fetch_all_filings(cik)
        except Exception as e:
            print(f"  ERR {label_prefix} (CIK {cik}): {e}")
            continue

        candidates = [f for f in filings if _is_10k_family(f["form"])]
        if not candidates:
            print(f"  SKIP {label_prefix} (CIK {cik}): no 10-K family filing found")
            continue

        if target_year is None:
            picked = sorted(candidates, key=lambda f: f["filing_date"], reverse=True)[0]
        else:
            in_year = [
                f for f in candidates
                if f["period_of_report"] and f["period_of_report"].startswith(str(target_year))
            ]
            if not in_year:
                in_year = [
                    f for f in candidates
                    if f["filing_date"].startswith(str(target_year))
                    or f["filing_date"].startswith(str(target_year + 1))
                ]
            if not in_year:
                print(f"  SKIP {label_prefix}: no 10-K with period_of_report {target_year} found")
                continue
            picked = sorted(in_year, key=lambda f: f["period_of_report"] or "")[0]

        record = {
            "cik": cik,
            "accession_number": picked["accession"],
            "label": f"{name} — {picked['filing_date']} ({picked['form']})",
            "category": categories,
            "filing_date": picked["filing_date"],
            "period_of_report": picked["period_of_report"],
        }
        new_lines.append(json.dumps(record, ensure_ascii=False))
        print(f"  ✓ {label_prefix}: {picked['accession']} period={picked['period_of_report']} form={picked['form']}")

    print("\n--- Append to eval/fixtures/filings.jsonl: ---")
    for line in new_lines:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
