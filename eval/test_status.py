"""Test extractor.status_detect against a piece of text or a fixture's item.

Usage:
    # paste-style (heredoc): pipe text in
    cat snippet.txt | .venv/bin/python eval/test_status.py 10

    # call /extract and re-classify a specific item, useful when iterating
    # on status_detect rules:
    .venv/bin/python eval/test_status.py 10 --label "BRK"
    .venv/bin/python eval/test_status.py 10 --cik 1067983 --accession 0001193125-26-083899

Reports matched regex (if any), the resulting status, and the relevant
text head/tail so you can see *why* the rule fired or didn't.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.status_detect import (
    _INCORPORATED_RE,
    _NOT_APPLICABLE_RE,
    _RESERVED_RE,
    detect_status,
)


def _load_fixtures() -> list[dict]:
    fp = ROOT / "eval" / "fixtures" / "filings.jsonl"
    return [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]


def _find_by_label(substring: str) -> dict | None:
    s = substring.lower()
    for fx in _load_fixtures():
        if s in fx["label"].lower():
            return fx
    return None


async def _fetch_item_text(url: str, body: dict, item_number: str) -> str | None:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{url}/extract", json=body)
        r.raise_for_status()
        for it in r.json()["items"]:
            if it["item_number"] == item_number:
                return it["content_text"]
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("item_number")
    ap.add_argument("--label")
    ap.add_argument("--cik")
    ap.add_argument("--accession")
    ap.add_argument("--url", default="http://localhost:8000")
    args = ap.parse_args()

    if args.label or (args.cik and args.accession):
        if args.label:
            fx = _find_by_label(args.label)
            if not fx:
                print(f"No fixture matched: {args.label!r}", file=sys.stderr)
                return 2
            body = {"file_url": fx["file_url"]} if "file_url" in fx else {
                "cik": fx["cik"], "accession_number": fx["accession_number"]
            }
        else:
            body = {"cik": args.cik, "accession_number": args.accession}
        text = await _fetch_item_text(args.url, body, args.item_number)
        if text is None:
            print(f"Item {args.item_number} not found in response", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()
        if not text.strip():
            print("Pipe text via stdin or pass --label / --cik+--accession", file=sys.stderr)
            return 2

    head_500 = text.strip()[:500]
    head_1500 = text.strip()[:1500]

    print(f"length: {len(text.strip())} chars")
    print(f"head[:160]: {head_500[:160]!r}")
    print()

    res_match = _RESERVED_RE.search(head_1500)
    na_match = _NOT_APPLICABLE_RE.search(head_1500)
    ibr_match = _INCORPORATED_RE.search(head_500)

    print(f"_RESERVED_RE     in head_1500 → {res_match}")
    print(f"_NOT_APPLICABLE_RE in head_1500 → {na_match}")
    print(f"_INCORPORATED_RE in head_500 → {ibr_match}"
          + (f"  @pos={ibr_match.start()}" if ibr_match else ""))
    print()
    status = detect_status(args.item_number, text)
    print(f"=> detect_status({args.item_number!r}) = {status!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
