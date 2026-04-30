"""FastAPI server: thin orchestration over extractor.pipeline."""

import asyncio
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, model_validator

from extractor.pipeline import (
    OversizedFilingError,
    UnsupportedFormError,
    extract_filing,
)


app = FastAPI(title="SEC 10-K Extractor", version="0.1.0")


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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


_INDEX_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>SEC 10-K Extractor</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 1em}
input{padding:.4em;font-size:1em;width:14em}
button{padding:.5em 1em;font-size:1em}
pre{background:#f5f5f5;padding:1em;overflow:auto;max-height:80vh}
</style></head>
<body>
<h1>SEC 10-K Extractor</h1>
<p>POST <code>/extract</code> with <code>{"cik","accession_number"}</code> or <code>{"file_url"}</code>.</p>
<form id="f">
  <label>CIK <input name="cik" placeholder="320193"></label>
  <label>Accession <input name="accession_number" placeholder="0000320193-24-000123"></label>
  <button>Extract</button>
</form>
<pre id="out">(no result yet)</pre>
<script>
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  document.getElementById('out').textContent = '...';
  try {
    const r = await fetch('/extract', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(data)});
    document.getElementById('out').textContent = JSON.stringify(await r.json(), null, 2);
  } catch (err) {
    document.getElementById('out').textContent = 'Error: ' + err;
  }
};
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return _INDEX_HTML
