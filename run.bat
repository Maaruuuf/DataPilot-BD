@echo off
cd /d %~dp0
if not exist .env copy .env.example .env
start "DataPilot API" cmd /k uvicorn backend.main:app --port 8000
timeout /t 3 >nul
set API_URL=http://localhost:8000
streamlit run frontend\app.py
