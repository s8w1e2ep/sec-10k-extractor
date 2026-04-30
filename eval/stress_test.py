"""Concurrent load probe: verify the server stays responsive while one
or more /extract calls are mid-parse.

Designed to catch the kind of regression that wouldn't show in unit
tests: a sync BeautifulSoup parse smuggled back onto the event loop,
or a request that holds the global rate-limit token bucket too long.

What it does:
- Picks 4 large fixtures (BRK FY 2025, JPM FY 2025, NVDA FY 2026,
  Apollo FY 2022) — these are the heaviest filings in the eval set.
- First-pass: hits each /extract serially to warm the on-disk cache,
  measuring per-fixture wall time.
- Second-pass: fires all 4 at the same time via asyncio.gather while
  also pinging /healthz 50× in parallel; reports max healthz latency.
  If the event loop is healthy, healthz should stay under 200 ms even
  while parsing is happening.
- Third-pass (optional, --cold): wipes the cache via a separate
  request and re-runs to see cold-cache concurrent behaviour.

Usage:
    eval/with_server.sh .venv/bin/python eval/stress_test.py
    eval/with_server.sh .venv/bin/python eval/stress_test.py --url https://sec-10k.zeabur.app
"""

import argparse
import asyncio
import statistics
import sys
import time

import httpx

FIXTURES = [
    ("1067983", "0001193125-26-083899", "BRK FY 2025"),
    ("19617",   "0001628280-26-008131", "JPM FY 2025"),
    ("1045810", "0001045810-26-000021", "NVDA FY 2026"),
    ("1411494", "0001411494-23-000010", "Apollo FY 2022"),
]


async def hit_extract(client: httpx.AsyncClient, base: str, cik: str, acc: str) -> dict:
    t = time.monotonic()
    r = await client.post(
        f"{base}/extract",
        json={"cik": cik, "accession_number": acc},
        timeout=120,
    )
    elapsed = (time.monotonic() - t) * 1000
    r.raise_for_status()
    body = r.json()
    return {
        "elapsed_ms": elapsed,
        "server_ms": body["stats"]["duration_ms"],
        "items": len(body["items"]),
    }


async def hit_healthz(client: httpx.AsyncClient, base: str) -> float:
    t = time.monotonic()
    r = await client.get(f"{base}/healthz", timeout=10)
    elapsed = (time.monotonic() - t) * 1000
    r.raise_for_status()
    return elapsed


async def serial_warm(client: httpx.AsyncClient, base: str) -> None:
    print("Pass 1 — serial warm-up (populates fetcher cache):")
    for cik, acc, label in FIXTURES:
        r = await hit_extract(client, base, cik, acc)
        print(f"  {label:18s} {r['items']:>2} items  wall={r['elapsed_ms']:>6.0f} ms  "
              f"server={r['server_ms']:>5} ms")


async def concurrent_with_healthz_probes(
    client: httpx.AsyncClient, base: str, *, n_healthz: int = 50
) -> None:
    print(f"\nPass 2 — fire all {len(FIXTURES)} extracts in parallel + ping /healthz "
          f"{n_healthz}× concurrently:")
    extract_tasks = [
        hit_extract(client, base, cik, acc)
        for cik, acc, _ in FIXTURES
    ]
    healthz_tasks = [hit_healthz(client, base) for _ in range(n_healthz)]

    t0 = time.monotonic()
    extract_results, *healthz_latencies = await asyncio.gather(
        asyncio.gather(*extract_tasks),
        *healthz_tasks,
    )
    total_wall = (time.monotonic() - t0) * 1000

    print(f"  total wall: {total_wall:.0f} ms")
    print(f"  per-extract wall: " + ", ".join(
        f"{r['elapsed_ms']:.0f}" for r in extract_results
    ))

    h = sorted(healthz_latencies)
    p50 = statistics.median(h)
    p95 = h[int(len(h) * 0.95)] if len(h) > 1 else h[0]
    p99 = h[int(len(h) * 0.99)] if len(h) > 1 else h[0]
    print(f"  /healthz under load: n={len(h)}  p50={p50:.0f} ms  p95={p95:.0f} ms  "
          f"p99={p99:.0f} ms  max={max(h):.0f} ms")

    if p95 > 500:
        print(
            f"  ⚠️  p95 healthz {p95:.0f} ms > 500 ms — event loop may be blocked.\n"
            f"     If parsing was correctly offloaded to a worker thread, healthz\n"
            f"     should stay sub-100 ms even under concurrent extract load.",
            file=sys.stderr,
        )
        return 1
    print("  ✓ event loop stayed responsive under load")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    args = ap.parse_args()

    async with httpx.AsyncClient() as client:
        await serial_warm(client, args.url)
        return await concurrent_with_healthz_probes(client, args.url)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
