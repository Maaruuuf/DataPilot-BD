"""Central configuration (environment driven)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", BASE_DIR / "storage"))
(STORAGE_DIR / "datasets").mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{STORAGE_DIR / 'datapilot.db'}")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
MODEL_MAIN = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b")

# Manual-effort estimates (minutes) used by the "time saved" counter.
MANUAL_MINUTES = {
    "per_row_entry": 1.5,       # typing/checking one record by hand
    "per_row_validation": 0.6,  # eyeballing one record for errors
    "report": 45.0,             # writing a management report
    "letter": 25.0,             # drafting an official letter
    "minutes": 40.0,            # writing meeting minutes
    "invoice": 20.0,            # preparing one invoice
    "allowance_sheet": 90.0,    # monthly flying allowance sheet
    "license_check": 60.0,      # manual expiry review of the staff list
}
