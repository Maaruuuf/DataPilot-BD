import os, tempfile, sys
from pathlib import Path
tmp = tempfile.mkdtemp(prefix="dp_test_")
os.environ["STORAGE_DIR"] = tmp
os.environ["GROQ_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(scope="session")
def client():
    from backend.main import app
    with TestClient(app) as c:
        yield c
