import json
from pathlib import Path
from types import SimpleNamespace as NS

from backend.core.llm import set_llm, LLM

SD = Path(__file__).resolve().parent.parent / "sample_data"
H = {"X-User": "tester"}


def upload(client, fname="engineering_staff_records_50.csv"):
    with open(SD / fname, "rb") as f:
        r = client.post("/datasets/upload", files={"file": (fname, f, "text/csv")}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- features 1-5, 9
def test_health_offline(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["ai_enabled"] is False


def test_upload_profile(client):
    d = upload(client)
    assert d["dataset"]["rows"] == 50 and d["profile"]["columns"] == 25
    kinds = {c["name"]: c["kind"] for c in d["profile"]["column_info"]}
    assert kinds["mobile"] == "phone" and kinds["email"] == "email" and kinds["license_expiry_date"] == "date"
    assert kinds["riding_engineer"] == "boolean" and kinds["annual_leave_balance"] == "integer"


def test_validation_catches_all_seeded_errors(client):
    ds = upload(client)["dataset"]["id"]
    r = client.post(f"/datasets/{ds}/validate", json={}, headers=H).json()
    found = {(i["record"], i["rule"]) for i in r["issues"]}
    expected = {("BG-ENG-1004", "bd_mobile"), ("BG-ENG-1006", "bd_mobile"), ("BG-ENG-1010", "not_null"),
                ("BG-ENG-1015", "date_order"), ("BG-ENG-1018", "range"), ("BG-ENG-1020", "date"),
                ("BG-ENG-1024", "bd_nid"), ("BG-ENG-1027", "consistent_naming"), ("BG-ENG-1032", "consistent_naming"),
                ("BG-ENG-1037", "not_null"), ("BG-ENG-1041", "outlier"), ("BG-ENG-1042", "implies"),
                ("BG-ENG-1047", "email"), ("BG-ENG-1050", "duplicate_rows")}
    assert expected <= found, expected - found
    assert len({i["record"] for i in r["issues"]}) == 14, "no false positives expected on clean rows"
    assert not any(i["column"] == "nid" and i["record"] in ("BG-ENG-1008", "BG-ENG-1022", "BG-ENG-1035") for i in r["issues"])
    assert 80 <= r["score"]["overall"] <= 95


def test_correction_flow_and_versioning(client):
    ds = upload(client)["dataset"]["id"]
    sug = client.post(f"/datasets/{ds}/corrections/suggest", json={}, headers=H).json()
    assert len(sug) == 5
    bn = next(s for s in sug if s["column"] == "mobile")
    assert bn["new_value"] == "+8801712345678"
    # reject the duplicate-row deletion, approve the rest
    dup = next(s for s in sug if s["new_value"] == "__DELETE_ROW__")
    client.post(f"/datasets/{ds}/corrections/decide", json={"ids": [dup["id"]], "decision": "rejected"}, headers=H)
    client.post(f"/datasets/{ds}/corrections/decide", json={"decision": "approved", "min_confidence": 0.85}, headers=H)
    res = client.post(f"/datasets/{ds}/corrections/apply", headers=H).json()
    assert res["new_version"] == 2 and res["applied"] == 4 and res["rows"] == 50
    prev = client.get(f"/datasets/{ds}/preview?rows=60&version=2").json()["rows"]
    assert prev[5]["mobile"] == "+8801712345678" and prev[19]["join_date"] == "2018-03-15"
    old = client.get(f"/datasets/{ds}/preview?rows=60&version=1").json()["rows"]
    assert old[19]["join_date"] == "15/03/2018", "original version must stay untouched"
    # nothing approved -> clean error
    assert client.post(f"/datasets/{ds}/corrections/apply", headers=H).status_code == 400
    csv = client.get(f"/datasets/{ds}/download").content.decode("utf-8-sig")
    assert "2018-03-15" in csv


def test_natural_language_rules_offline(client):
    ds = upload(client)["dataset"]["id"]
    r = client.post(f"/datasets/{ds}/rules/nl", json={
        "text": "annual leave balance must be between 0 and 28, mobile must be a valid BD number, email must be unique",
        "save": True}, headers=H).json()
    types = {(x["type"], x["column"]) for x in r["rules"]}
    assert ("range", "annual_leave_balance") in types and ("bd_mobile", "mobile") in types and ("unique", "email") in types
    v = client.post(f"/datasets/{ds}/validate", json={}, headers=H).json()
    assert v["rules_applied"] > 80
    assert len(client.get(f"/datasets/{ds}/rules").json()) == 3


def test_pipeline_report_bangla_and_audit(client):
    ds = upload(client)["dataset"]["id"]
    p = client.post(f"/datasets/{ds}/pipeline", json={"lang": "bn"}, headers=H).json()
    assert p["suggestions"] == 5 and p["report_id"]
    html = client.get(f"/documents/{p['report_id']}/html").text
    assert "ডেটা মান ও বিশ্লেষণ প্রতিবেদন" in html and "<svg" in html
    en = client.post(f"/datasets/{ds}/report", json={"lang": "en", "use_ai": False}, headers=H).json()
    assert "Data Quality" in en["html"]
    log = client.get(f"/audit?dataset_id={ds}").json()
    assert {"upload", "validate", "pipeline", "generate_report"} <= {a["action"] for a in log}
    assert all(a["actor"] == "tester" for a in log)
    s = client.get("/stats").json()
    assert s["minutes_saved"] > 0 and s["reduction_pct"] > 90


# ---------------------------------------------------------------- feature 6 (offline parser + privacy tokens)
def test_smart_entry_offline(client):
    text = ("Name: Rakib Hasan\nDesignation: Engineer\nJoin date: 15/03/2026\nMobile: ০১৭১২৩৪৫৬৭৮\nEmail: rakib@x.com\nNID: 1234567890\n\n"
            "Name: Test Two\nDesignation: Technician\nJoin date: 99/99/2026\nMobile: 12345")
    r = client.post("/extract", json={"text": text, "schema_name": "staff_joining"}, headers=H).json()
    assert len(r["rows"]) == 2 and r["method"] == "rules"
    assert r["rows"][0]["join_date"] == "2026-03-15" and r["rows"][0]["mobile"] == "+8801712345678"
    assert any("invalid date" in w for w in r["warnings"]) and any("invalid BD mobile" in w for w in r["warnings"])
    saved = client.post("/extract/save", json={"rows": r["rows"][:1], "name": "joiners"}, headers=H).json()
    assert saved["dataset"]["rows"] == 1


# ---------------------------------------------------------------- feature 7: chat tool loop with a fake LLM
class FakeLLM(LLM):
    def __init__(self, script):
        self.client, self.script, self.seen = object(), list(script), []

    @property
    def available(self):
        return True

    def chat(self, messages, **kw):
        self.seen.append(messages)
        return self.script.pop(0)


def tool_msg(args):
    return NS(content="", tool_calls=[NS(id="c1", function=NS(name="query_data", arguments=json.dumps(args)))])


def test_chat_tool_loop_and_privacy(client):
    ds = upload(client)["dataset"]["id"]
    fake = FakeLLM([
        tool_msg({"operation": "aggregate", "column": "flying_hours_oct_2026", "agg": "sum", "group_by": ["base"], "chart": "bar"}),
        tool_msg({"operation": "aggregate", "column": "nonexistent", "agg": "sum"}),          # model makes an error ...
        tool_msg({"operation": "filter", "columns": ["full_name", "mobile"], "filters": [{"column": "riding_engineer", "op": "==", "value": "Y"}], "limit": 3}),
        NS(content="Done. See the table.", tool_calls=None)])
    set_llm(fake)
    try:
        r = client.post(f"/datasets/{ds}/chat", json={"message": "hours by base?"}, headers=H).json()
    finally:
        set_llm(LLM(""))
    assert r["answer"] == "Done. See the table."
    kinds = [a["type"] for a in r["artifacts"]]
    assert kinds.count("table") == 2 and kinds.count("chart") == 1
    # error was fed back to the model, not crashed
    tool_payloads = [m["content"] for ms in fake.seen for m in ms if m.get("role") == "tool"]
    assert any("Unknown column" in p for p in tool_payloads)
    # privacy: the model saw masked mobiles, the user sees real ones
    shown = [m["content"] for m in fake.seen[-1] if m.get("role") == "tool"][-1]
    assert "***" in shown and "+88017" not in shown.replace("+88017*", "")
    user_table = r["artifacts"][-1]["data"]
    assert len(user_table) == 3 and all("*" not in row["mobile"] for row in user_table)


def test_chat_offline_message(client):
    ds = upload(client)["dataset"]["id"]
    r = client.post(f"/datasets/{ds}/chat", json={"message": "hi"}, headers=H).json()
    assert "GROQ_API_KEY" in r["answer"]


# ---------------------------------------------------------------- features 11-14
def test_license_tracker(client):
    ds = upload(client)["dataset"]["id"]
    r = client.post(f"/datasets/{ds}/licenses", json={"reference_date": "2026-11-03"}, headers=H).json()
    assert set(r["items_found"]) == {"License", "CAAB Pass", "Recurrent Training"}
    assert r["counts"]["expired"] > 0 and r["counts"]["critical"] > 0
    assert r["rows"][0]["status"] == "expired" and r["rows"][0]["days_left"] < 0
    assert r["staff_needing_urgent_action"] > 0
    assert {e["missing"] for e in r["expat_gaps"]} == {"Work Permit", "Visa"} and len(r["expat_gaps"]) == 6  # 3 expats x 2
    assert r["rows"][0]["coordinate_with"] in ("QA Department", "Security Department", "QA / Training")


def test_flying_allowance_and_excel(client, tmp_path):
    ds = upload(client)["dataset"]["id"]
    body = {"month": "October 2026", "hourly_rate": 1000, "sector_rate": 200, "cap_hours": 100}
    r = client.post(f"/datasets/{ds}/flying-allowance", json=body, headers=H).json()
    assert r["headcount"] > 5
    expected = sum(min(x["flying_hours"], 100) * 1000 + x["sectors"] * 200 for x in r["rows"])
    assert abs(r["total"] - expected) < 0.01
    ex = {e["emp_id"]: e["issue"] for e in r["exceptions"]}
    assert "not marked" in ex["BG-ENG-1042"] and "not marked" in ex["BG-ENG-1041"]  # 412h non-rider is NOT paid
    assert "BG-ENG-1041" not in {x["emp_id"] for x in r["rows"]}
    low = client.post(f"/datasets/{ds}/flying-allowance", json={**body, "cap_hours": 50}, headers=H).json()
    assert any("exceed cap" in e["issue"] for e in low["exceptions"])
    assert all(x["payable_hours"] <= 50 for x in low["rows"])
    x = client.post(f"/datasets/{ds}/flying-allowance/excel", json=body, headers=H)
    assert x.status_code == 200
    p = tmp_path / "fa.xlsx"
    p.write_bytes(x.content)
    from openpyxl import load_workbook
    wb = load_workbook(p)
    assert wb.sheetnames == ["Flying Allowance", "Exceptions"]
    assert str(wb["Flying Allowance"]["G9"].value).startswith("=MIN(E9")
    (tmp_path / "path.txt").write_text(str(p))


def test_letters_and_minutes(client):
    r = client.post("/letters/draft", json={"letter_type": "qa_extension", "fields": {
        "staff_name": "Md. Rafiq", "emp_id": "BG-ENG-1001", "document": "B1 License", "doc_no": "BD-AME-12345",
        "expiry_date": "2026-12-01", "requested_extension": "2027-06-01"}, "use_ai": False}, headers=H).json()
    assert "BD-AME-12345" in r["text"] and "up to 2027-06-01" in r["body"] and r["method"] == "template"
    assert "[justification]" in r["body"], "missing facts become visible placeholders, never invented"
    bn = client.post("/letters/draft", json={"letter_type": "general", "fields": {"subject": "X", "points": "Y"}, "lang": "bn"}, headers=H).json()
    assert "মহোদয়" in bn["text"]
    d = client.post("/letters/docx", json={"text": r["text"]})
    assert d.status_code == 200 and d.content[:2] == b"PK"
    assert client.post("/letters/draft", json={"letter_type": "nope", "fields": {}}, headers=H).status_code == 400
    notes = ("Reviewed expiring licences.\nAgreed: QA to extend 3 licences for 90 days.\n"
             "Rahim will send the extension letters by Thursday.\nKarim must renew CAAB passes before 15 Nov.\nGeneral discussion on rosters.")
    m = client.post("/minutes", json={"notes": notes, "title": "Weekly Coordination", "attendees": ["Rahim", "Karim"]}, headers=H).json()
    assert len(m["actions"]) == 2 and m["actions"][0]["owner"] == "Rahim" and "Thursday" in m["actions"][0]["deadline"]
    assert len(m["decisions"]) == 1
    items = client.get("/action-items?status=open").json()
    assert len(items) >= 2
    assert client.patch(f"/action-items/{items[0]['id']}", json={"status": "done"}, headers=H).json()["status"] == "done"


def test_invoices_full_flow(client):
    with open(SD / "partner_airline_invoices_30.csv", "rb") as f:
        imp = client.post("/invoices/import", files={"file": ("i.csv", f, "text/csv")}, headers=H).json()
    assert imp["created"] == 29 and imp["skipped"][0]["reason"] == "missing partner or amount"
    inv = client.post("/invoices", json={"partner": "Emirates", "country": "UAE", "currency": "USD", "exchange_rate": 110,
                                         "items": [{"description": "AOG support", "quantity": 2, "unit_price": 1500}],
                                         "vat_pct": 0, "issue_date": "2026-01-10", "terms_days": 30}, headers=H).json()
    assert inv["total"] == 3000 and inv["total_bdt"] == 330000 and inv["effective_status"] == "overdue"
    assert inv["number"].startswith("INV-2026-")
    page = client.get(f"/invoices/{inv['id']}/html").text
    assert "৳3,30,000" in page, "lakh/crore BDT formatting"
    s = client.get("/invoices/summary").json()
    assert s["outstanding_bdt"] > 0 and "Emirates" in s["by_partner"] and sum(s["aging_bdt"].values()) > 0
    st = client.patch(f"/invoices/{inv['id']}/status", json={"status": "submitted_to_accounts"}, headers=H).json()
    assert st["submitted_date"]
    paid = client.patch(f"/invoices/{inv['id']}/status", json={"status": "paid"}, headers=H).json()
    assert paid["effective_status"] == "paid"
    rem = client.post(f"/invoices/{inv['id']}/reminder?use_ai=false", headers=H).json()
    assert "payment" in rem["subject"].lower()
    assert client.patch(f"/invoices/{inv['id']}/status", json={"status": "bogus"}, headers=H).status_code == 400
