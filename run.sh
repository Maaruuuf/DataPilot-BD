#!/usr/bin/env bash
# Starts the FastAPI backend (port 8000) and the Streamlit UI (port 8501).
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
uvicorn backend.main:app --port 8001 &
API_PID=$!
trap "kill $API_PID" EXIT
sleep 2
API_URL=${API_URL:-http://localhost:8001} streamlit run frontend/app.py
