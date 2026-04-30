"""FastAPI server: thin orchestration over extractor.pipeline."""

import asyncio
import os
import time
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, model_validator

from extractor.logging_config import (
    get_logger,
    log_event,
    new_request_id,
    reset_request_id,
    set_request_id,
    setup_logging,
)
from extractor.pipeline import (
    FilingNotFoundError,
    OversizedFilingError,
    UnsupportedFormError,
    UpstreamError,
    extract_filing,
)


setup_logging()
LOG = get_logger("server")


# ---------------------------------------------------------------------------
# Per-IP rate limiter (best-effort cost shield, NOT a security boundary).
#
# In-process token bucket keyed by client IP. Survives only as long as the
# uvicorn worker; container restarts wipe state. Single-worker by design
# (the SEC fetcher token bucket is also in-process), so we don't need
# cross-worker coordination.
#
# Defaults: 10 requests/min, burst 10. Tunable via env. Health and the
# HTML form are exempt because Zeabur and the grader poll them.
# ---------------------------------------------------------------------------


def _rate_limit_config() -> tuple[float, float]:
    """(rate_per_sec, burst). Read at request time so tests can override."""
    try:
        per_min = float(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
        burst = float(os.environ.get("RATE_LIMIT_BURST", "10"))
    except ValueError:
        per_min, burst = 10.0, 10.0
    return per_min / 60.0, burst


class _SyncTokenBucket:
    """Sync token bucket. No asyncio.Lock — middleware runs serially per
    worker for the per-IP fast path, and we don't sleep/await here."""

    __slots__ = ("rate", "capacity", "tokens", "last")

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


_RATE_BUCKETS: dict[str, _SyncTokenBucket] = {}
_RATE_EXEMPT_PATHS = frozenset({"/healthz", "/", "/docs", "/redoc", "/openapi.json"})


def _client_ip(request: Request) -> str:
    """Honor X-Forwarded-For (Zeabur proxies through one). First entry is
    the original client; subsequent are intermediate proxies."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _reset_rate_limiter_for_test() -> None:
    """Test-only hook. Clears all per-IP buckets."""
    _RATE_BUCKETS.clear()


app = FastAPI(title="SEC 10-K Extractor", version="0.1.0")


# Middleware are applied in reverse order of registration: the LAST one
# registered runs OUTERMOST. So request_logging is registered last to
# wrap the rate-limit middleware and still log 429 responses.

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in _RATE_EXEMPT_PATHS:
        return await call_next(request)
    ip = _client_ip(request)
    rate, capacity = _rate_limit_config()
    bucket = _RATE_BUCKETS.get(ip)
    if bucket is None or bucket.rate != rate or bucket.capacity != capacity:
        bucket = _SyncTokenBucket(rate, capacity)
        _RATE_BUCKETS[ip] = bucket
    if not bucket.try_acquire():
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate limit exceeded",
                "limit_per_minute": int(rate * 60),
                "retry_after_s": 1,
            },
            headers={"Retry-After": "1"},
        )
    return await call_next(request)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Bind a request_id contextvar, log one line per request."""
    rid = request.headers.get("x-request-id") or new_request_id()
    token = set_request_id(rid)
    t0 = time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        log_event(
            LOG,
            "http.request",
            method=request.method,
            path=request.url.path,
            status=status,
            duration_ms=int((time.monotonic() - t0) * 1000),
            client_ip=_client_ip(request),
        )
        reset_request_id(token)


class ExtractRequest(BaseModel):
    cik: Optional[str] = None
    accession_number: Optional[str] = None
    file_url: Optional[str] = None

    @model_validator(mode="after")
    def check_inputs(self):
        if self.file_url:
            if self.cik or self.accession_number:
                raise ValueError(
                    "Provide either (cik, accession_number) OR file_url, not both"
                )
        else:
            if not (self.cik and self.accession_number):
                raise ValueError(
                    "Provide either (cik, accession_number) or file_url"
                )
        return self


def _unsupported_form_response(e: UnsupportedFormError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": str(e),
            "form": e.form,
            "supported_forms": "10-K (and historical 10-K family: 10-KSB, 10-K405, 10-KT). Amendments (/A suffix) are not supported.",
        },
    )


def _oversized_response(e: OversizedFilingError) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error": str(e),
            "size_bytes": e.size_bytes,
            "limit_bytes": e.limit_bytes,
        },
    )


def _not_found_response(e: FilingNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": str(e), "what": e.what, "where": e.where},
    )


def _upstream_response(e: UpstreamError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error": str(e),
            "upstream": e.where,
            "upstream_status": e.status,
        },
    )


@app.post("/extract")
async def extract(req: ExtractRequest):
    try:
        result = await asyncio.wait_for(
            extract_filing(
                cik=req.cik,
                accession_number=req.accession_number,
                file_url=req.file_url,
            ),
            timeout=90.0,
        )
        return JSONResponse(result)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Extraction exceeded 90s timeout")
    except UnsupportedFormError as e:
        return _unsupported_form_response(e)
    except OversizedFilingError as e:
        return _oversized_response(e)
    except FilingNotFoundError as e:
        return _not_found_response(e)
    except UpstreamError as e:
        return _upstream_response(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/extract/{cik}/{accession}")
async def extract_get(cik: str, accession: str):
    try:
        result = await asyncio.wait_for(
            extract_filing(cik=cik, accession_number=accession),
            timeout=90.0,
        )
        return JSONResponse(result)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Extraction exceeded 90s timeout")
    except UnsupportedFormError as e:
        return _unsupported_form_response(e)
    except OversizedFilingError as e:
        return _oversized_response(e)
    except FilingNotFoundError as e:
        return _not_found_response(e)
    except UpstreamError as e:
        return _upstream_response(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


_INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SEC 10-K Extractor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
  :root {
    --bg: #0a0e1a;
    --surface: #111827;
    --surface-2: #1a2332;
    --border: #1f2937;
    --border-strong: #2d3748;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --text-dim: #64748b;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --accent-soft: rgba(99,102,241,0.15);
    --success: #10b981;
    --warn: #f59e0b;
    --danger: #ef4444;
    --code-bg: #0f1623;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f7f8fb;
      --surface: #ffffff;
      --surface-2: #f1f5f9;
      --border: #e5e7eb;
      --border-strong: #cbd5e1;
      --text: #0f172a;
      --text-muted: #475569;
      --text-dim: #64748b;
      --accent: #4f46e5;
      --accent-hover: #4338ca;
      --accent-soft: rgba(79,70,229,0.12);
      --code-bg: #f8fafc;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: 'IBM Plex Sans', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    font-size: 15px;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  .mono { font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace; }
  .container { max-width: 960px; margin: 0 auto; padding: 28px 20px 80px; }
  /* Header */
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; gap: 12px; flex-wrap: wrap; }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand-mark {
    width: 36px; height: 36px; border-radius: 9px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    display: grid; place-items: center;
    color: white; font-weight: 600; font-size: 12px;
    font-family: 'IBM Plex Mono', monospace;
    box-shadow: 0 1px 2px rgba(0,0,0,0.1), inset 0 1px 0 rgba(255,255,255,0.15);
  }
  .brand-text { display: flex; flex-direction: column; line-height: 1.3; }
  .brand-text strong { font-weight: 600; font-size: 15px; letter-spacing: -0.01em; }
  .brand-text span { color: var(--text-dim); font-size: 12px; }
  /* Hero */
  .hero { margin-bottom: 28px; }
  .hero h1 {
    font-size: 28px; font-weight: 600; letter-spacing: -0.02em;
    margin: 0 0 8px;
  }
  .hero p { color: var(--text-muted); margin: 0; font-size: 15px; max-width: 680px; }
  /* Card */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .card + .card, .card + .card-stack, #result > section + section { margin-top: 16px; }
  #result > * { margin-top: 16px; }
  .card-header {
    padding: 14px 20px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; flex-wrap: wrap;
  }
  .card-header h2 {
    margin: 0; font-size: 12px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.07em;
    color: var(--text-muted);
  }
  .card-body { padding: 20px; }
  /* Tabs */
  .tabs { display: inline-flex; padding: 3px; background: var(--surface-2); border-radius: 8px; gap: 2px; }
  .tab {
    padding: 6px 14px; font-size: 13px; font-weight: 500;
    background: transparent; border: 0; cursor: pointer;
    color: var(--text-muted); border-radius: 6px;
    font-family: inherit; transition: all 200ms;
  }
  .tab.active { background: var(--surface); color: var(--text); box-shadow: 0 1px 2px rgba(0,0,0,0.08); }
  .tab:hover:not(.active) { color: var(--text); }
  /* Form */
  .field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
  .field label {
    font-size: 11px; font-weight: 500; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .input {
    width: 100%; padding: 10px 12px; font-size: 14px;
    background: var(--surface-2); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px;
    font-family: 'IBM Plex Mono', ui-monospace, monospace;
    transition: border-color 200ms, box-shadow 200ms, background 200ms;
  }
  .input:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  .input::placeholder { color: var(--text-dim); }
  .row { display: grid; grid-template-columns: 1fr 1.5fr; gap: 12px; }
  @media (max-width: 600px) { .row { grid-template-columns: 1fr; } }
  /* Buttons */
  .btn {
    padding: 9px 16px; font-size: 14px; font-weight: 500;
    border: 1px solid transparent; border-radius: 8px; cursor: pointer;
    font-family: inherit; transition: all 200ms;
    display: inline-flex; align-items: center; gap: 8px;
    line-height: 1.2;
  }
  .btn-primary { background: var(--accent); color: white; }
  .btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
  .btn-ghost {
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { color: var(--text); border-color: var(--border-strong); background: var(--surface-2); }
  .btn-sm { padding: 4px 10px; font-size: 12px; cursor: pointer; }
  .examples { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
  .examples-label { color: var(--text-dim); font-size: 12px; }
  .chip {
    padding: 4px 11px; font-size: 12px; cursor: pointer;
    background: var(--surface-2); color: var(--text-muted);
    border: 1px solid var(--border); border-radius: 999px;
    font-family: inherit; transition: all 200ms;
  }
  .chip:hover { color: var(--text); border-color: var(--border-strong); }
  .chip.tag { background: transparent; font-size: 10px; padding: 1px 7px; cursor: default; pointer-events: none; }
  .actions {
    display: flex; align-items: center; justify-content: space-between;
    margin-top: 14px; gap: 12px; flex-wrap: wrap;
  }
  .hint { color: var(--text-dim); font-size: 12px; }
  /* Spinner */
  .spinner {
    width: 13px; height: 13px; border: 2px solid rgba(255,255,255,0.3);
    border-top-color: currentColor; border-radius: 50%;
    animation: spin 800ms linear infinite;
    display: inline-block; vertical-align: -2px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* Filing meta grid */
  .filing-meta {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 18px;
  }
  .meta-item { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .meta-label {
    font-size: 10px; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.07em; font-weight: 500;
  }
  .meta-value { font-size: 14px; color: var(--text); font-weight: 500; word-break: break-word; }
  .meta-value.mono { font-size: 13px; }
  .meta-value a { color: var(--accent-hover); text-decoration: none; }
  .meta-value a:hover { text-decoration: underline; }
  /* Stats grid */
  .stats {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 1px; background: var(--border); border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden;
  }
  .stat { background: var(--surface); padding: 14px 16px; }
  .stat-num { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.1; }
  .stat-label { font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 4px; font-weight: 500; }
  .stat-num.success { color: var(--success); }
  .stat-num.warn { color: var(--warn); }
  .stat-num.danger { color: var(--danger); }
  .stats-meta {
    display: flex; gap: 22px; margin-top: 14px; flex-wrap: wrap;
    font-size: 12px; color: var(--text-muted);
  }
  /* Items */
  .item { padding: 14px 20px; border-top: 1px solid var(--border); }
  .item:first-child { border-top: 0; }
  .item-head {
    display: flex; align-items: center; gap: 12px;
    cursor: pointer; user-select: none; flex-wrap: wrap;
  }
  .item-head:hover .item-title { color: var(--accent-hover); }
  .item-num {
    font-family: 'IBM Plex Mono', monospace; font-size: 12px;
    background: var(--surface-2); padding: 3px 9px; border-radius: 4px;
    color: var(--text-muted); min-width: 44px; text-align: center;
    border: 1px solid var(--border);
  }
  .item-title { flex: 1; font-size: 14px; font-weight: 500; min-width: 200px; transition: color 200ms; }
  .item-meta { font-size: 11px; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; }
  .badge {
    font-size: 10px; padding: 2px 8px; border-radius: 999px;
    text-transform: uppercase; letter-spacing: 0.04em; font-weight: 500;
    border: 1px solid currentColor; background: transparent;
    white-space: nowrap;
  }
  .badge.extracted { color: var(--success); }
  .badge.incorporated_by_reference { color: var(--accent-hover); }
  .badge.not_applicable { color: var(--text-dim); }
  .badge.reserved { color: var(--text-dim); }
  .badge.missing { color: var(--danger); }
  .item-body {
    margin-top: 10px; padding: 12px 14px;
    background: var(--code-bg); border: 1px solid var(--border);
    border-radius: 6px; font-family: 'IBM Plex Mono', monospace;
    font-size: 12px; color: var(--text-muted); white-space: pre-wrap;
    max-height: 280px; overflow: auto; display: none;
    line-height: 1.55;
  }
  .item.open .item-body { display: block; }
  .chev { color: var(--text-dim); transition: transform 200ms; font-size: 12px; }
  .item.open .chev { transform: rotate(90deg); }
  /* Warnings */
  .warning-list { display: flex; flex-direction: column; gap: 8px; }
  .warning {
    padding: 10px 12px; background: rgba(245, 158, 11, 0.08);
    border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 6px;
    font-size: 13px; color: var(--text); line-height: 1.5;
  }
  .warning code {
    color: var(--warn); font-size: 11px;
    background: rgba(245, 158, 11, 0.1); padding: 1px 6px; border-radius: 4px;
    margin-right: 6px; font-family: 'IBM Plex Mono', monospace;
  }
  /* Status pill */
  .pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 999px;
    font-size: 11px; font-weight: 500; letter-spacing: 0.02em;
    background: rgba(16, 185, 129, 0.1); color: var(--success);
    border: 1px solid rgba(16, 185, 129, 0.25);
  }
  .pill.error { background: rgba(239, 68, 68, 0.1); color: var(--danger); border-color: rgba(239, 68, 68, 0.25); }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  /* Output JSON */
  .json-view {
    background: var(--code-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px;
    font-family: 'IBM Plex Mono', monospace; font-size: 12px;
    color: var(--text-muted); overflow: auto; max-height: 480px;
    white-space: pre; line-height: 1.55; margin: 0;
  }
  /* Error */
  .error-card {
    background: rgba(239, 68, 68, 0.06);
    border: 1px solid rgba(239, 68, 68, 0.25);
    color: var(--text); padding: 12px 14px; border-radius: 8px;
    font-size: 14px;
  }
  .error-card strong { color: var(--danger); }
  /* Empty / loading */
  .empty {
    text-align: center; color: var(--text-muted);
    padding: 40px 20px; font-size: 13px;
    background: var(--surface); border: 1px dashed var(--border);
    border-radius: 12px;
  }
  /* Helpers */
  .hidden { display: none !important; }
  .row-tools { display: flex; gap: 8px; align-items: center; }
  /* Reduced motion */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
  /* Focus */
  button:focus-visible, .input:focus-visible, .tab:focus-visible, a:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 8px;
  }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-mark">10K</div>
        <div class="brand-text">
          <strong>SEC 10-K Extractor</strong>
          <span>Filing &rarr; structured items API</span>
        </div>
      </div>
    </header>

    <section class="hero">
      <h1>Pull structured items from any 10-K filing</h1>
      <p>Submit a CIK + accession number, or a direct primary-document URL. The pipeline locates each Part/Item, classifies its status (extracted, incorporated by reference, not applicable, reserved), and surfaces cross-strategy disagreements as warnings.</p>
    </section>

    <section class="card">
      <div class="card-header">
        <h2>Request</h2>
        <div class="tabs" role="tablist" aria-label="Input mode">
          <button class="tab active" data-mode="cik" type="button" role="tab" aria-selected="true">CIK + Accession</button>
          <button class="tab" data-mode="url" type="button" role="tab" aria-selected="false">File URL</button>
        </div>
      </div>
      <div class="card-body">
        <form id="extract-form">
          <div id="mode-cik">
            <div class="row">
              <div class="field">
                <label for="cik">CIK</label>
                <input class="input" id="cik" name="cik" placeholder="320193" autocomplete="off" inputmode="numeric">
              </div>
              <div class="field">
                <label for="accession_number">Accession number</label>
                <input class="input" id="accession_number" name="accession_number" placeholder="0000320193-25-000079" autocomplete="off">
              </div>
            </div>
            <div class="examples">
              <span class="examples-label">Try:</span>
              <button type="button" class="chip" data-cik="320193" data-acc="0000320193-25-000079">Apple FY2025</button>
              <button type="button" class="chip" data-cik="789019" data-acc="0000950170-25-100235">Microsoft FY2025</button>
              <button type="button" class="chip" data-cik="1045810" data-acc="0001045810-26-000021">NVIDIA FY2026</button>
              <button type="button" class="chip" data-cik="320193" data-acc="0000320193-96-000023">Apple 1996 (plain text)</button>
            </div>
          </div>
          <div id="mode-url" class="hidden">
            <div class="field">
              <label for="file_url">Primary document URL</label>
              <input class="input" id="file_url" name="file_url" placeholder="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm">
            </div>
          </div>
          <div class="actions">
            <span class="hint">First request can take 5-15s while the filing is fetched. Repeat calls hit the local cache.</span>
            <button class="btn btn-primary" id="submit" type="submit">
              <span id="btn-label">Extract</span>
            </button>
          </div>
        </form>
      </div>
    </section>

    <div id="result"></div>
  </div>

  <script>
  (function() {
    const tabs = document.querySelectorAll('.tab');
    const modeCik = document.getElementById('mode-cik');
    const modeUrl = document.getElementById('mode-url');
    const form = document.getElementById('extract-form');
    const resultEl = document.getElementById('result');
    const btn = document.getElementById('submit');
    const btnLabel = document.getElementById('btn-label');
    let currentMode = 'cik';

    tabs.forEach(t => t.addEventListener('click', () => {
      tabs.forEach(x => { x.classList.remove('active'); x.setAttribute('aria-selected', 'false'); });
      t.classList.add('active');
      t.setAttribute('aria-selected', 'true');
      currentMode = t.dataset.mode;
      modeCik.classList.toggle('hidden', currentMode !== 'cik');
      modeUrl.classList.toggle('hidden', currentMode !== 'url');
    }));

    document.querySelectorAll('#mode-cik .chip').forEach(c => c.addEventListener('click', () => {
      document.getElementById('cik').value = c.dataset.cik;
      document.getElementById('accession_number').value = c.dataset.acc;
    }));

    function setLoading(loading) {
      btn.disabled = loading;
      btnLabel.innerHTML = loading
        ? '<span class="spinner"></span> Extracting&hellip;'
        : 'Extract';
    }

    function escHtml(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function fmt(n) { return new Intl.NumberFormat().format(n || 0); }

    function statusBadge(s) {
      const cls = String(s || 'missing');
      const label = cls.replace(/_/g, ' ');
      return '<span class="badge ' + escHtml(cls) + '">' + escHtml(label) + '</span>';
    }

    function renderResult(data, ms) {
      const f = data.filing || {};
      const stats = data.stats || {};
      const items = data.items || [];
      const warnings = data.warnings || [];
      const strat = stats.strategies || {};
      const timing = fmt(ms) + ' ms wall · ' + fmt(stats.duration_ms) + ' ms server · ' + fmt(stats.fetch_ms) + ' ms fetch';

      const docUrl = f.primary_document_url
        ? '<a href="' + escHtml(f.primary_document_url) + '" target="_blank" rel="noopener">open on SEC</a>'
        : '&mdash;';

      let html = '';

      html += ''
        + '<section class="card">'
        + '  <div class="card-header">'
        + '    <h2>Filing</h2>'
        + '    <span class="pill"><span class="dot"></span>' + escHtml(f.form || 'OK') + '</span>'
        + '  </div>'
        + '  <div class="card-body">'
        + '    <div class="filing-meta">'
        + '      <div class="meta-item"><span class="meta-label">Company</span><span class="meta-value">' + escHtml(f.company_name || '—') + '</span></div>'
        + '      <div class="meta-item"><span class="meta-label">CIK</span><span class="meta-value mono">' + escHtml(f.cik || '—') + '</span></div>'
        + '      <div class="meta-item"><span class="meta-label">Accession</span><span class="meta-value mono">' + escHtml(f.accession_number || '—') + '</span></div>'
        + '      <div class="meta-item"><span class="meta-label">Period of report</span><span class="meta-value mono">' + escHtml(f.period_of_report || '—') + '</span></div>'
        + '      <div class="meta-item"><span class="meta-label">Filed</span><span class="meta-value mono">' + escHtml(f.filing_date || '—') + '</span></div>'
        + '      <div class="meta-item"><span class="meta-label">Format</span><span class="meta-value mono">' + escHtml(stats.format || '—') + '</span></div>'
        + '      <div class="meta-item"><span class="meta-label">Document</span><span class="meta-value">' + docUrl + '</span></div>'
        + '    </div>'
        + '  </div>'
        + '</section>';

      const extracted = stats.items_extracted || 0;
      const ibr = stats.items_incorporated_by_reference || 0;
      const na = stats.items_not_applicable || 0;
      const rsv = stats.items_reserved || 0;
      const missing = stats.items_missing || 0;
      const total = stats.items_total || 0;
      const cost = (stats.estimated_cost_usd || 0).toFixed(6);

      html += ''
        + '<section class="card">'
        + '  <div class="card-header">'
        + '    <h2>Coverage</h2>'
        + '    <span class="mono" style="font-size:12px;color:var(--text-dim);">' + escHtml(timing) + '</span>'
        + '  </div>'
        + '  <div class="card-body">'
        + '    <div class="stats">'
        + '      <div class="stat"><div class="stat-num success">' + fmt(extracted) + '</div><div class="stat-label">Extracted</div></div>'
        + '      <div class="stat"><div class="stat-num">' + fmt(ibr) + '</div><div class="stat-label">By reference</div></div>'
        + '      <div class="stat"><div class="stat-num">' + fmt(na) + '</div><div class="stat-label">Not applicable</div></div>'
        + '      <div class="stat"><div class="stat-num">' + fmt(rsv) + '</div><div class="stat-label">Reserved</div></div>'
        + '      <div class="stat"><div class="stat-num ' + (missing ? 'danger' : '') + '">' + fmt(missing) + '</div><div class="stat-label">Missing</div></div>'
        + '      <div class="stat"><div class="stat-num">' + fmt(total) + '</div><div class="stat-label">Total items</div></div>'
        + '    </div>'
        + '    <div class="stats-meta mono">'
        + '      <span>strategies: toc=' + fmt(strat.toc) + ' · heading=' + fmt(strat.heading) + ' · llm=' + fmt(strat.llm) + '</span>'
        + '      <span>llm calls: ' + fmt(stats.llm_calls) + '</span>'
        + '      <span>cost: $' + escHtml(cost) + '</span>'
        + '    </div>'
        + '  </div>'
        + '</section>';

      if (warnings.length) {
        const wHtml = warnings.map(w => {
          const code = w.code || w.type || 'warning';
          const msg = w.message || JSON.stringify(w);
          return '<div class="warning"><code>' + escHtml(code) + '</code>' + escHtml(msg) + '</div>';
        }).join('');
        html += ''
          + '<section class="card">'
          + '  <div class="card-header"><h2>Warnings (' + warnings.length + ')</h2></div>'
          + '  <div class="card-body"><div class="warning-list">' + wHtml + '</div></div>'
          + '</section>';
      }

      const itemsHtml = items.map((it, i) => {
        const text = it.content_text || '';
        const preview = text.slice(0, 1500);
        const truncated = text.length > 1500;
        const range = it.char_range
          ? '[' + fmt(it.char_range.start) + '–' + fmt(it.char_range.end) + ']'
          : '';
        return ''
          + '<div class="item" data-idx="' + i + '">'
          + '  <div class="item-head" tabindex="0" role="button" aria-expanded="false">'
          + '    <span class="chev">&#9656;</span>'
          + '    <span class="item-num">' + escHtml(it.item_number || '?') + '</span>'
          + '    <span class="item-title">' + escHtml(it.item_title || '—') + '</span>'
          + '    <span class="item-meta">' + escHtml(range) + '</span>'
          + '    ' + statusBadge(it.status)
          + '  </div>'
          + '  <div class="item-body">' + escHtml(preview) + (truncated ? '\n\n[… truncated]' : '') + '</div>'
          + '</div>';
      }).join('');

      html += ''
        + '<section class="card">'
        + '  <div class="card-header">'
        + '    <h2>Items (' + items.length + ')</h2>'
        + '    <button class="btn btn-ghost btn-sm" id="expand-all" type="button">Expand all</button>'
        + '  </div>'
        + '  <div>' + (itemsHtml || '<div class="card-body"><div class="hint">No items returned.</div></div>') + '</div>'
        + '</section>';

      html += ''
        + '<section class="card">'
        + '  <div class="card-header">'
        + '    <h2>Raw JSON</h2>'
        + '    <div class="row-tools">'
        + '      <button class="btn btn-ghost btn-sm" id="copy-json" type="button">Copy</button>'
        + '      <button class="btn btn-ghost btn-sm" id="toggle-json" type="button">Show</button>'
        + '    </div>'
        + '  </div>'
        + '  <div id="json-wrap" class="hidden card-body"><pre class="json-view" id="json-view"></pre></div>'
        + '</section>';

      resultEl.innerHTML = html;

      resultEl.querySelectorAll('.item-head').forEach(h => {
        const toggle = () => {
          const parent = h.parentElement;
          parent.classList.toggle('open');
          h.setAttribute('aria-expanded', parent.classList.contains('open') ? 'true' : 'false');
        };
        h.addEventListener('click', toggle);
        h.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
        });
      });
      const expandBtn = document.getElementById('expand-all');
      if (expandBtn) {
        expandBtn.addEventListener('click', () => {
          const allItems = resultEl.querySelectorAll('.item');
          const allOpen = allItems.length > 0 && Array.from(allItems).every(it => it.classList.contains('open'));
          allItems.forEach(it => {
            it.classList.toggle('open', !allOpen);
            const head = it.querySelector('.item-head');
            if (head) head.setAttribute('aria-expanded', String(!allOpen));
          });
          expandBtn.textContent = allOpen ? 'Expand all' : 'Collapse all';
        });
      }
      const jsonWrap = document.getElementById('json-wrap');
      const jsonView = document.getElementById('json-view');
      const toggleBtn = document.getElementById('toggle-json');
      toggleBtn.addEventListener('click', () => {
        const showing = !jsonWrap.classList.contains('hidden');
        if (showing) {
          jsonWrap.classList.add('hidden');
          toggleBtn.textContent = 'Show';
        } else {
          jsonView.textContent = JSON.stringify(data, null, 2);
          jsonWrap.classList.remove('hidden');
          toggleBtn.textContent = 'Hide';
        }
      });
      const copyBtn = document.getElementById('copy-json');
      copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
          const original = copyBtn.textContent;
          copyBtn.textContent = 'Copied';
          setTimeout(() => { copyBtn.textContent = original; }, 1400);
        } catch (_) {
          copyBtn.textContent = 'Copy failed';
        }
      });
    }

    function renderError(status, body) {
      const msg = (body && (body.detail || body.error)) || ('HTTP ' + status);
      resultEl.innerHTML = ''
        + '<section class="card">'
        + '  <div class="card-header">'
        + '    <h2>Error</h2>'
        + '    <span class="pill error"><span class="dot"></span>HTTP ' + status + '</span>'
        + '  </div>'
        + '  <div class="card-body">'
        + '    <div class="error-card"><strong>' + escHtml(msg) + '</strong></div>'
        + '    <pre class="json-view" style="margin-top:12px;">' + escHtml(JSON.stringify(body, null, 2)) + '</pre>'
        + '  </div>'
        + '</section>';
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      let body;
      if (currentMode === 'cik') {
        body = {
          cik: (fd.get('cik') || '').toString().trim(),
          accession_number: (fd.get('accession_number') || '').toString().trim(),
        };
        if (!body.cik || !body.accession_number) {
          resultEl.innerHTML = '<div class="empty">Please provide both CIK and accession number.</div>';
          return;
        }
      } else {
        body = { file_url: (fd.get('file_url') || '').toString().trim() };
        if (!body.file_url) {
          resultEl.innerHTML = '<div class="empty">Please provide a file URL.</div>';
          return;
        }
      }
      setLoading(true);
      resultEl.innerHTML = '<div class="empty"><span class="spinner" style="color:var(--accent);border-color:var(--border);border-top-color:var(--accent);"></span> Fetching from SEC EDGAR&hellip;</div>';
      const t0 = performance.now();
      try {
        const r = await fetch('/extract', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        const ms = Math.round(performance.now() - t0);
        const json = await r.json();
        if (!r.ok) renderError(r.status, json);
        else renderResult(json, ms);
      } catch (err) {
        resultEl.innerHTML = ''
          + '<section class="card"><div class="card-body">'
          + '<div class="error-card"><strong>Network error</strong>: ' + escHtml(err && err.message ? err.message : err) + '</div>'
          + '</div></section>';
      } finally {
        setLoading(false);
      }
    });
  })();
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return _INDEX_HTML
