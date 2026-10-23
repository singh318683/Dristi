#!/usr/bin/env bash
# Start Drishti locally. Optional: export LLM_API_KEY / LLM_BASE_URL / LLM_MODEL first.
cd "$(dirname "$0")/server"
PORT="${PORT:-8792}"
exec ../.venv/bin/uvicorn app:app --host 0.0.0.0 --port "$PORT"
