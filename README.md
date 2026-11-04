# ✈️ DataPilot BD

**A multi-agent AI system that replaces manual data entry, validation and report writing, built around the
day-to-day work of an airline Engineering Department in Bangladesh.**

Stack: **Python · FastAPI · Streamlit · SQLAlchemy (SQLite / any SQL DB) · pandas · Groq `openai/gpt-oss-120b`**



---

## Quick start

```vs code powershell
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env                                   # then paste your GROQ_API_KEY

uvicorn backend.main:app --port 8001
$env:API_URL="http://localhost:8001"
streamlit run frontend/app.py                                             
```
* UI: http://localhost:8501  ·  API docs (Swagger): http://localhost:8001/docs
* Without a key the app still runs in **offline mode** (rule-based engine); with a key every AI feature switches on.
* Upload `sample_data/engineering_staff_records_50.csv`, then `sample_data/partner_airline_invoices_30.csv` (Invoices tab).

## Architecture

```
Streamlit UI  ──HTTP──▶  FastAPI  ──▶  Orchestrator ─▶ Intake → Validation → Correction → Reporting
                                          │                          ↑ human approves
                                          ├─▶ Chat agent (tool-calling, read-only pandas executor)
                                          ├─▶ Extraction agent (text → typed rows)
                                          ├─▶ Modules: Licenses · Flying allowance · Letters/Minutes · Invoices
                                          ├─▶ SQL (SQLAlchemy): datasets, versions, rules, corrections, audit, invoices…
                                          └─▶ Groq LLM wrapper (graceful offline fallback everywhere)
```

## The 14 features

| # | Feature | Where |
|---|---|---|
| 1 | Smart upload (CSV/Excel), auto schema & type detection | `agents/intake.py` |
| 2 | Profiling dashboard + 0-100 Data Quality Score (4 dimensions) | `intake.py`, `validation.py` |
| 3 | Hybrid validation: ~18 rule types auto-inferred + optional AI semantic checks | `agents/validation.py` |
| 4 | Natural-language rules ("age between 18 and 60") → executable rules | `validation.nl_to_rules` |
| 5 | Correction agent with confidence scores; **human approve/reject**; new version on apply | `agents/correction.py` |
| 6 | Smart Entry: messy text → schema-checked rows (invoice, travel, joiner, order, custom) | `agents/extraction.py` |
| 7 | Chat with your CSV: LLM calls one safe `query_data` tool; numbers come from pandas, never from the model | `agents/chat.py` |
| 8 | One-click AI executive report (English/বাংলা), inline-SVG charts, print to PDF | `agents/reporting.py` |
| 9 | SQL audit trail + dataset versioning + time-saved counter | `database.py`, `orchestrator.py` |
| 10 | Bangladesh localisation: +880 mobiles & operators, NID, 64 districts, Bangla digits, ৳ lakh/crore, bilingual output | `core/localization.py` |
| 11 | **License / certificate / CAAB pass / training expiry tracker** + QA/Security/Admin coordination letters | `modules/license_tracker.py` |
| 12 | **Monthly flying-allowance sheet** (live Excel formulas, exceptions sheet) | `modules/flying_allowance.py` |
| 13 | **Official letters (8 types, DOCX) + meeting minutes + follow-up action tracker** | `modules/letters.py` |
| 14 | **Partner-airline invoices, payment tracking, aging, reminders** | `modules/invoices.py` |

Extras: **Privacy mode** (PII masked / tokenised before any text reaches the LLM), 2 realistic sample datasets with
seeded errors, and 18 automated tests.

## Design decisions 
* **Human-in-the-loop.** The AI only *proposes*; nothing changes until approved, and the original version is never overwritten.
* **No hallucinated numbers.** The chat model has one read-only structured tool; every figure shown is computed by pandas. Errors are fed back so it can self-correct.
* **Privacy by design.** Column masking for samples/tool results; reversible tokens (`<PHONE_1>`) for free text. The user still sees real values locally.
* **Conservative automation.** Auto-fixes cover only unambiguous cases (Bangla digits, formats, capitalisation). Ambiguous values (e.g. `Technician (B1)` vs `(B2)`) are never merged: a bug caught by the test-suite during development.
* **Domain rules.** A non-riding engineer with flying hours is an error; expatriates legitimately have no NID; expiry cannot precede issue.
* **Graceful degradation.** Every AI feature has a deterministic fallback, so demos work offline and tests need no API key.

## Tests
```bash
pytest -q          # 18 tests: validators, API flows, chat tool loop (fake LLM), Excel, invoices, privacy
```


