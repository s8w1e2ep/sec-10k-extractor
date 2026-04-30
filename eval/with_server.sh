#!/usr/bin/env bash
# Ensure a local uvicorn instance is up on :8000, run the given command,
# then tear down the server (only if this script started it).
#
# Usage:
#   eval/with_server.sh <command> [args...]
#
# Examples:
#   eval/with_server.sh .venv/bin/python eval/run_eval.py
#   eval/with_server.sh .venv/bin/python eval/inspect_filing.py --label "JPM" 10 11
#   eval/with_server.sh .venv/bin/python eval/test_status.py 10 --label "BRK"
#
# Reads SEC_CONTACT_EMAIL from the env (uses ha9mv8c@gmail.com as default).
# CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY are passed through unchanged.

set -e

PORT=8000
HEALTH_URL="http://localhost:${PORT}/healthz"

if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    "$@"
    exit $?
fi

export SEC_CONTACT_EMAIL="${SEC_CONTACT_EMAIL:-ha9mv8c@gmail.com}"
.venv/bin/python -m uvicorn server.main:app --port "$PORT" >/tmp/uvicorn-eval.log 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 40); do
    if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
        break
    fi
    sleep 0.25
done

if ! curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Server failed to come up on :$PORT — see /tmp/uvicorn-eval.log" >&2
    tail -20 /tmp/uvicorn-eval.log >&2
    exit 1
fi

"$@"
