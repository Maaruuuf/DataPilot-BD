"""DataPilot BD - Streamlit UI (talks to the FastAPI backend over HTTP)."""
import os
from contextlib import contextmanager
from datetime import date

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8001").rstrip("/")
st.set_page_config(page_title="DataPilot BD", page_icon="✈️", layout="wide")


# ------------------------------------------------------------------------------------------ helpers
def call(method, path, **kw):
    headers = {"X-User": st.session_state.get("user", "analyst")}
    try:
        r = requests.request(method, API + path, headers=headers, timeout=180, **kw)
    except requests.ConnectionError:
        st.error(f"Cannot reach the backend at {API}. Start it with: uvicorn backend.main:app --reload")
        st.stop()
    if r.status_code >= 400:
        try:
            msg = r.json().get("detail", r.text)
        except Exception:
            msg = r.text
        st.error(f"{msg}")
        return None
    return r


def get(path, **kw):
    r = call("GET", path, **kw)
    return r.json() if r is not None else None


def post(path, payload=None, **kw):
    r = call("POST", path, json=payload if payload is not None else {}, **kw)
    return r.json() if r is not None else None


def explain(text, bn=None):
    st.caption(bn if (bn and st.session_state.get("lang") == "bn") else text)


def chart(artifact):
    df = pd.DataFrame(artifact["data"])
    fn = {"bar": px.bar, "line": px.line, "pie": None}.get(artifact["kind"])
    if artifact["kind"] == "pie":
        fig = px.pie(df, names=artifact["x"], values=artifact["y"])
    else:
        fig = fn(df, x=artifact["x"], y=artifact["y"])
    st.plotly_chart(fig, width="stretch")


class NoDataset(Exception):
    pass


def need_dataset():
    if not st.session_state.get("ds_id"):
        raise NoDataset()
    return st.session_state["ds_id"]


@contextmanager
def tab(i):
    """A tab body that needs a dataset shows a hint instead of stopping the whole page."""
    with tabs[i]:
        try:
            yield
        except NoDataset:
            st.info("Upload or select a dataset in the sidebar first (try the files in sample_data/).")


# ------------------------------------------------------------------------------------------ sidebar
health = get("/health") or {}
with st.sidebar:
    st.title("✈️ DataPilot BD")
    st.caption("AI agents for data entry, validation & reporting")
    st.session_state["user"] = st.text_input("Your name (for the audit trail)", st.session_state.get("user", "analyst"))
    lang_label = st.radio("Language / ভাষা", ["English", "বাংলা"], horizontal=True)
    st.session_state["lang"] = "bn" if lang_label == "বাংলা" else "en"
    privacy = st.toggle("🔒 Privacy mode (mask phone/email/NID before AI)", value=True)
    ai_on = health.get("ai_enabled", False)
    use_ai = st.toggle("🤖 Use AI (Groq gpt-oss)", value=ai_on, disabled=not ai_on)
    if ai_on:
        st.success(f"AI online: {health.get('model')}")
    else:
        st.warning("Offline mode: rule-based engine only. Add GROQ_API_KEY to enable AI.")
    st.divider()
    datasets = get("/datasets") or []
    options = {f"#{d['id']} · {d['name']} (v{d['version']}, {d['rows']} rows)": d["id"] for d in datasets}
    if options:
        pick = st.selectbox("Active dataset", list(options))
        st.session_state["ds_id"] = options[pick]
    up = st.file_uploader("Upload CSV / Excel", type=["csv", "xlsx", "xls"])
    if up is not None and st.session_state.get("last_upload") != (up.name, up.size):
        r = call("POST", "/datasets/upload", files={"file": (up.name, up.getvalue())})
        if r is not None:
            st.session_state["last_upload"] = (up.name, up.size)
            st.session_state["ds_id"] = r.json()["dataset"]["id"]
            st.rerun()
    stats = get("/stats") or {}
    st.divider()
    st.metric("⏱️ Time saved so far", f"{stats.get('hours_saved', 0)} h", f"{stats.get('reduction_pct', 0)}% faster than manual")

lang = st.session_state["lang"]
ds_id = st.session_state.get("ds_id")
st.title("DataPilot BD · Engineering Department Assistant")

tabs = st.tabs(["📂 Data & Profile", "✅ Validate & Fix", "🧾 Smart Entry", "💬 Chat", "📊 Report",
                "🛂 Licenses", "💰 Flying Allowance", "✉️ Letters & Minutes", "🧮 Invoices", "🕓 Audit"])

# ------------------------------------------------------------------------------------------ 1. data & profile
with tab(0):
    ds_id = need_dataset()
    info = get(f"/datasets/{ds_id}")
    prof = get(f"/datasets/{ds_id}/profile")
    c = st.columns(5)
    c[0].metric("Rows", prof["rows"]); c[1].metric("Columns", prof["columns"])
    c[2].metric("Missing cells", f"{prof['missing_pct']}%"); c[3].metric("Duplicate rows", prof["duplicate_rows"])
    c[4].metric("Version", info["version"])
    if info.get("last_validation"):
        st.success(f"Last Data Quality Score: **{info['last_validation']['score']['overall']}/100** (grade {info['last_validation']['score']['grade']})")
    prev = get(f"/datasets/{ds_id}/preview", params={"rows": 200})
    st.subheader("Preview")
    st.dataframe(pd.DataFrame(prev["rows"]), width="stretch", height=300)
    st.subheader("Auto-detected schema")
    cols = pd.DataFrame(prof["column_info"])[["name", "kind", "null_pct", "unique", "sample"]]
    st.dataframe(cols, width="stretch", height=300)
    miss = cols[cols["null_pct"] > 0]
    if len(miss):
        st.plotly_chart(px.bar(miss, x="name", y="null_pct", title="Missing values by column (%)"), width="stretch")
    st.download_button("⬇️ Download current version (CSV)",
                       call("GET", f"/datasets/{ds_id}/download").content, f"dataset_{ds_id}_v{info['version']}.csv", "text/csv")
    with st.expander("Version history"):
        st.dataframe(pd.DataFrame(info["versions"]), width="stretch")

# ------------------------------------------------------------------------------------------ 2. validate & fix
with tab(1):
    ds_id = need_dataset()
    b1, b2 = st.columns(2)
    if b1.button("▶️ Run validation", type="primary"):
        st.session_state["val"] = post(f"/datasets/{ds_id}/validate", {"use_ai": use_ai, "lang": lang})
    if b2.button("⚡ One-click pipeline (validate → suggest fixes → report)"):
        p = post(f"/datasets/{ds_id}/pipeline", {"use_ai": use_ai, "lang": lang, "privacy": privacy})
        if p:
            st.success(f"Done in {p['seconds']}s · score {p['score']['overall']} · {p['suggestions']} fixes suggested · report #{p['report_id']}")
            st.session_state["val"] = post(f"/datasets/{ds_id}/validate", {"use_ai": False, "lang": lang})
    val = st.session_state.get("val")
    if val and val.get("version") == (get(f"/datasets/{ds_id}") or {}).get("version"):
        sc = val["score"]
        m = st.columns(5)
        m[0].metric("Data Quality Score", f"{sc['overall']}/100", f"Grade {sc['grade']}", delta_color="off")
        m[1].metric("Completeness", f"{sc['completeness']}%"); m[2].metric("Validity", f"{sc['validity']}%")
        m[3].metric("Uniqueness", f"{sc['uniqueness']}%"); m[4].metric("Consistency", f"{sc['consistency']}%")
        st.caption(f"{val['rules_applied']} checks applied · {val['summary']['total']} issues in {val['summary']['rows_affected']} rows "
                   f"· {val['summary']['fixable']} auto-fixable")
        iss = pd.DataFrame(val["issues"])
        if len(iss):
            f1, f2 = st.columns(2)
            sev = f1.multiselect("Severity", ["error", "warning"], default=["error", "warning"])
            rl = f2.multiselect("Check", sorted(iss["rule"].unique()), default=sorted(iss["rule"].unique()))
            view = iss[iss["severity"].isin(sev) & iss["rule"].isin(rl)]
            st.dataframe(view[["row_number", "record", "column", "rule", "severity", "message", "value", "fixable"]],
                         width="stretch", height=300)
    st.divider()
    st.subheader("Natural-language rules")
    explain("Describe your rules in plain English; the AI turns them into executable checks.",
            "সাধারণ ভাষায় নিয়ম লিখুন; AI সেগুলোকে যাচাই-নিয়মে রূপান্তর করবে।")
    nl = st.text_area("e.g. mobile must be a valid BD number, annual leave balance between 0 and 28, email must be unique")
    n1, n2 = st.columns(2)
    if n1.button("Preview rules") and nl:
        st.session_state["nl_prev"] = post(f"/datasets/{ds_id}/rules/nl", {"text": nl})
    if st.session_state.get("nl_prev"):
        st.json(st.session_state["nl_prev"])
        if n2.button("Save these rules"):
            post(f"/datasets/{ds_id}/rules/nl", {"text": nl, "save": True}); st.success("Saved. Re-run validation."); st.rerun()
    saved = get(f"/datasets/{ds_id}/rules") or []
    if saved:
        with st.expander(f"{len(saved)} saved custom rules"):
            for r in saved:
                a, b = st.columns([6, 1]); a.code(f"{r['rule']}")
                if b.button("🗑", key=f"delr{r['id']}"):
                    call("DELETE", f"/rules/{r['id']}"); st.rerun()

    st.divider()
    st.subheader("AI auto-correction · human approval")
    explain("Nothing changes until you approve. The original version is always kept.",
            "আপনি অনুমোদন না করা পর্যন্ত কিছুই পরিবর্তন হয় না। মূল সংস্করণ সবসময় সংরক্ষিত থাকে।")
    if st.button("🔧 Suggest corrections"):
        post(f"/datasets/{ds_id}/corrections/suggest", {"use_ai": use_ai, "lang": lang, "privacy": privacy})
    corr = pd.DataFrame(get(f"/datasets/{ds_id}/corrections") or [])
    if len(corr):
        corr["new_value"] = corr["new_value"].replace("__DELETE_ROW__", "🗑 delete this duplicate row")
        edit = st.data_editor(
            corr[["id", "status", "row_number", "column", "old_value", "new_value", "confidence", "source", "reason"]],
            column_config={"status": st.column_config.SelectboxColumn("decision", options=["pending", "approved", "rejected", "applied"]),
                           "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1, format="%.2f")},
            disabled=["id", "row_number", "column", "old_value", "new_value", "confidence", "source", "reason"],
            hide_index=True, width="stretch", key="corr_editor")
        k1, k2, k3 = st.columns(3)
        if k1.button("✅ Approve all ≥ 90% confidence"):
            post(f"/datasets/{ds_id}/corrections/decide", {"decision": "approved", "min_confidence": 0.9}); st.rerun()
        if k2.button("💾 Save my decisions"):
            for dec in ("approved", "rejected"):
                ids = edit[(edit["status"] == dec) & (corr["status"] != dec)]["id"].tolist()
                if ids:
                    post(f"/datasets/{ds_id}/corrections/decide", {"ids": ids, "decision": dec})
            st.rerun()
        if k3.button("🚀 Apply approved → new version", type="primary"):
            res = post(f"/datasets/{ds_id}/corrections/apply")
            if res:
                st.success(f"Created version {res['new_version']} ({res['applied']} corrections, {res['rows']} rows)"); st.rerun()

# ------------------------------------------------------------------------------------------ 3. smart entry
SAMPLES = {
    "invoice": "Invoice No: EK-8841\nPartner: Emirates\nDate: 12/10/2026\nService: AOG support B777\nQuantity: 2\nUnit price: 4,500\nCurrency: USD\nTotal: 9,000",
    "travel_request": "Employee: Md. Rafiqul Islam\nID: BG-ENG-1001\nDestination: Dubai\nDeparture: 20/11/2026\nReturn: 25/11/2026\nPurpose: Borescope inspection on B777\nFlight: BG-147\nPassport: A01234567",
    "staff_joining": "Name: Sabbir Ahmed\nDesignation: Assistant Engineer\nJoin date: 01/12/2026\nMobile: ০১৭১২৩৪৫৬৭৮\nEmail: sabbir@example.com\nNID: 1234567890\nBank account: 1234-5678-90\nDepartment: Line Maintenance",
    "order": "Customer: Rahim Uddin\nMobile: 01912345678\nAddress: Mirpur, Dhaka\nItem: Safety shoes\nQuantity: 4\nPrice: 2,800",
}
with tab(2):
    st.subheader("Smart Entry: unstructured text → validated rows")
    explain("Paste an invoice, joining form, travel request or free-text message. Offline mode understands 'Key: value' lines; with AI it reads any free text.",
            "ইনভয়েস, জয়েনিং ফর্ম বা যেকোনো লেখা পেস্ট করুন; সিস্টেম সেটিকে যাচাইকৃত সারিতে রূপান্তর করবে।")
    sch = get("/extract/schemas") or {}
    schema = st.selectbox("Record type", list(sch))
    st.caption("Fields: " + ", ".join(f"{k}{'*' if v['required'] else ''}" for k, v in sch[schema].items()))
    text = st.text_area("Paste text", SAMPLES.get(schema, ""), height=200, key=f"ext_{schema}")
    if st.button("Extract", type="primary"):
        st.session_state["ext"] = post("/extract", {"text": text, "schema_name": schema, "lang": lang, "privacy": privacy})
    ext = st.session_state.get("ext")
    if ext:
        st.caption(f"Method: {ext['method']}")
        for w in ext["warnings"]:
            st.warning(w)
        rows = st.data_editor(pd.DataFrame(ext["rows"]), num_rows="dynamic", width="stretch", key="ext_rows")
        e1, e2 = st.columns(2)
        nm = e1.text_input("New dataset name", f"{schema}_entries")
        if e1.button("💾 Save as new dataset"):
            r = post("/extract/save", {"rows": rows.to_dict("records"), "name": nm})
            if r: st.success("Saved. Select it in the sidebar."); st.rerun()
        if ds_id and e2.button("➕ Append to the active dataset"):
            r = post("/extract/save", {"rows": rows.to_dict("records"), "name": nm, "append_to": ds_id})
            if r: st.success("Appended as a new version."); st.rerun()

# ------------------------------------------------------------------------------------------ 4. chat
with tab(3):
    ds_id = need_dataset()
    st.subheader("Chat with your data")
    explain("Answers come from real calculations on your file, not from the model's memory.",
            "উত্তরগুলো আপনার ফাইলের প্রকৃত হিসাব থেকে আসে, মডেলের অনুমান থেকে নয়।")
    key = f"chat_{ds_id}"
    hist = st.session_state.setdefault(key, [])
    if not hist:
        st.markdown("Try: *How many riding engineers per base?* · *Top 5 by flying hours* · *Which licenses expire before 2027-01-31?* · *Average leave balance by designation*")
    for m in hist:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            for a in m.get("artifacts", []):
                if a["type"] == "table":
                    st.dataframe(pd.DataFrame(a["data"]), width="stretch")
                elif a["type"] == "chart":
                    chart(a)
    q = st.chat_input("Ask a question about the uploaded data…")
    if q:
        hist.append({"role": "user", "content": q})
        with st.spinner("Analysing…"):
            res = post(f"/datasets/{ds_id}/chat", {"message": q, "lang": lang, "privacy": privacy,
                                                   "history": [{"role": h["role"], "content": h["content"]} for h in hist[:-1]]})
        if res:
            hist.append({"role": "assistant", "content": res["answer"], "artifacts": res["artifacts"]})
        st.rerun()
    if hist and st.button("Clear conversation"):
        st.session_state[key] = []; st.rerun()

# ------------------------------------------------------------------------------------------ 5. report
with tab(4):
    ds_id = need_dataset()
    st.subheader("One-click executive report")
    rl = st.radio("Report language", ["en", "bn"], format_func=lambda x: "English" if x == "en" else "বাংলা", horizontal=True)
    if st.button("📄 Generate report", type="primary"):
        st.session_state["report"] = post(f"/datasets/{ds_id}/report", {"lang": rl, "use_ai": use_ai})
    rep = st.session_state.get("report")
    if rep:
        st.caption(f"{rep['title']} · AI-written summary: {'yes' if rep['ai'] else 'no (rule-based)'}")
        st.download_button("⬇️ Download HTML (open and Print → Save as PDF)", rep["html"], f"report_{ds_id}.html", "text/html")
        st.iframe(rep["html"], height=900)

# ------------------------------------------------------------------------------------------ 11. licenses
ITEM_TO_LETTER = {"License": "qa_extension", "Authorization": "qa_extension", "Certificate": "qa_extension",
                  "Recurrent Training": "qa_extension", "CAAB Pass": "caab_pass_renewal", "D-Pass": "caab_pass_renewal",
                  "Work Permit": "visa_extension", "Visa": "visa_extension"}
with tab(5):
    ds_id = need_dataset()
    st.subheader("License, certificate, pass & training tracker")
    explain("Finds every expired or expiring item and tells you which department to coordinate with.")
    ref = st.date_input("Reference date", date.today())
    if st.button("Check expiries", type="primary"):
        st.session_state["lic"] = post(f"/datasets/{ds_id}/licenses", {"reference_date": ref.isoformat()})
    lic = st.session_state.get("lic")
    if lic:
        cts = lic["counts"]
        m = st.columns(6)
        for col, (k, lab) in zip(m, [("expired", "🔴 Expired"), ("critical", "🟠 ≤30 days"), ("warning", "🟡 ≤60 days"),
                                      ("upcoming", "🔵 ≤90 days"), ("missing", "⚪ Missing"), ("valid", "🟢 Valid")]):
            col.metric(lab, cts[k])
        st.caption(f"{lic['staff_needing_urgent_action']} staff need urgent action · items tracked: {', '.join(lic['items_found'])}")
        flt = st.multiselect("Show", ["expired", "critical", "warning", "upcoming", "missing", "valid"], default=["expired", "critical", "warning"])
        tbl = pd.DataFrame(lic["rows"])
        if len(tbl):
            view = tbl[tbl["status"].isin(flt)]
            st.dataframe(view[["emp_id", "name", "designation", "item", "expiry_date", "days_left", "status", "coordinate_with", "action"]],
                         width="stretch", height=320)
            st.plotly_chart(px.histogram(tbl[tbl["status"] != "valid"], x="item", color="status", title="Items needing attention"),
                            width="stretch")
            st.markdown("**Draft a coordination letter**")
            opts = {f"{r.emp_id} · {r.name} · {r.item} ({r.status})": r for r in view.itertuples()}
            if opts:
                sel = st.selectbox("Select a case", list(opts))
                if st.button("✉️ Draft letter"):
                    r = opts[sel]
                    lt = ITEM_TO_LETTER.get(r.item, "qa_extension")
                    fields = {"staff_name": r.name, "emp_id": r.emp_id, "document": r.item, "expiry_date": r.expiry_date,
                              "pass_type": r.item, "permit_expiry": r.expiry_date if r.item == "Work Permit" else "",
                              "visa_expiry": r.expiry_date if r.item == "Visa" else ""}
                    res = post("/letters/draft", {"letter_type": lt, "fields": fields, "lang": lang, "use_ai": use_ai, "privacy": privacy})
                    if res:
                        st.session_state["lic_letter"] = res
        if st.session_state.get("lic_letter"):
            st.text_area("Letter", st.session_state["lic_letter"]["text"], height=300, key="lic_letter_txt")
        if lic["expat_gaps"]:
            with st.expander(f"⚠️ {len(lic['expat_gaps'])} data gaps for expatriate AMEs (work permit / visa)"):
                st.dataframe(pd.DataFrame(lic["expat_gaps"]), width="stretch")

# ------------------------------------------------------------------------------------------ 12. flying allowance
with tab(6):
    ds_id = need_dataset()
    st.subheader("Monthly flying-allowance sheet")
    explain("Rates below are placeholders. Enter your department's approved rates.")
    a = st.columns(5)
    month = a[0].text_input("Month", "October 2026")
    hr = a[1].number_input("Rate / flying hour", 0.0, value=1500.0, step=50.0)
    sr = a[2].number_input("Rate / sector", 0.0, value=500.0, step=50.0)
    cap = a[3].number_input("Monthly cap (hours)", 0.0, value=100.0, step=5.0)
    cur = a[4].selectbox("Currency", ["BDT", "USD"])
    body = {"month": month, "hourly_rate": hr, "sector_rate": sr, "cap_hours": cap, "currency": cur, "prepared_by": st.session_state["user"]}
    if st.button("Calculate", type="primary"):
        st.session_state["fa"] = post(f"/datasets/{ds_id}/flying-allowance", body)
    fa = st.session_state.get("fa")
    if fa:
        m = st.columns(3)
        m[0].metric("Riding engineers paid", fa["headcount"]); m[1].metric("Total flying hours", fa["total_hours"])
        m[2].metric("Total allowance", f"{cur} {fa['total']:,.0f}")
        st.dataframe(pd.DataFrame(fa["rows"]), width="stretch")
        if fa["exceptions"]:
            st.warning(f"{len(fa['exceptions'])} exceptions need review before payment")
            st.dataframe(pd.DataFrame(fa["exceptions"]), width="stretch")
        x = call("POST", f"/datasets/{ds_id}/flying-allowance/excel", json=body)
        if x is not None:
            st.download_button("⬇️ Download Excel sheet (for Accounts)", x.content, f"flying_allowance_{month}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------------------------------------------------------------------------------ 13. letters & minutes
with tab(7):
    sub = st.tabs(["Official letter", "Meeting minutes & follow-ups"])
    with sub[0]:
        types = get("/letters/types") or {}
        lt = st.selectbox("Letter type", list(types), format_func=lambda k: types[k]["label"])
        vals = {}
        cols = st.columns(2)
        for i, f in enumerate(types[lt]["fields"]):
            vals[f] = cols[i % 2].text_input(f.replace("_", " ").capitalize(), key=f"lf_{lt}_{f}")
        sender = st.text_input("Sender name / designation", "Executive, Engineering")
        if st.button("Draft letter", type="primary"):
            st.session_state["letter"] = post("/letters/draft", {"letter_type": lt, "fields": vals, "lang": lang, "use_ai": use_ai,
                                                                  "privacy": privacy, "sender_name": sender})
        lt_res = st.session_state.get("letter")
        if lt_res:
            st.caption(f"Drafted by: {lt_res['method']} · missing facts appear as [placeholders]")
            txt = st.text_area("Edit before sending", lt_res["text"], height=360, key="letter_edit")
            d1, d2 = st.columns(2)
            dx = call("POST", "/letters/docx", json={"text": txt, "lang": lang})
            if dx is not None:
                d1.download_button("⬇️ Word (.docx)", dx.content, "official_letter.docx",
                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            d2.download_button("⬇️ Text", txt, "official_letter.txt")
    with sub[1]:
        mt = st.text_input("Meeting title", "Weekly Engineering Coordination")
        mc = st.columns(2)
        venue = mc[0].text_input("Venue", "Conference Room, GM-145 Office")
        att = mc[1].text_input("Attendees (comma separated)")
        notes = st.text_area("Paste rough notes", "Reviewed expiring licences.\nAgreed: QA to extend 3 licences for 90 days.\n"
                             "Rahim will send the extension letters by Thursday.\nKarim must renew CAAB passes before 15 Nov.", height=180)
        if st.button("Create minutes", type="primary"):
            st.session_state["minutes"] = post("/minutes", {"notes": notes, "title": mt, "venue": venue, "lang": lang, "privacy": privacy,
                                                            "attendees": [x.strip() for x in att.split(",") if x.strip()]})
        mn = st.session_state.get("minutes")
        if mn:
            st.text_area("Minutes", mn["text"], height=300)
            st.download_button("⬇️ Download minutes (.txt)", mn["text"], "minutes.txt")
        st.markdown("### Follow-up tracker")
        items = get("/action-items") or []
        if items:
            df = pd.DataFrame(items)
            ed = st.data_editor(df[["id", "action", "owner", "deadline", "status"]], hide_index=True, width="stretch",
                                disabled=["id", "action", "owner", "deadline"],
                                column_config={"status": st.column_config.SelectboxColumn(options=["open", "in_progress", "done"])},
                                key="act_ed")
            if st.button("Save status changes"):
                for r_old, r_new in zip(df.itertuples(), ed.itertuples()):
                    if r_old.status != r_new.status:
                        call("PATCH", f"/action-items/{r_new.id}", json={"status": r_new.status})
                st.rerun()

# ------------------------------------------------------------------------------------------ 14. invoices
with tab(8):
    st.subheader("Partner-airline invoices & payments")
    summ = get("/invoices/summary") or {}
    m = st.columns(4)
    m[0].metric("Invoices", summ.get("count", 0)); m[1].metric("Outstanding (BDT)", summ.get("outstanding_fmt", "৳0"))
    bs = summ.get("by_status", {})
    m[2].metric("Overdue", bs.get("overdue", {}).get("count", 0)); m[3].metric("Paid", bs.get("paid", {}).get("count", 0))
    if summ.get("count"):
        ag = pd.DataFrame({"bucket": list(summ["aging_bdt"]), "BDT": list(summ["aging_bdt"].values())})
        c1, c2 = st.columns(2)
        c1.plotly_chart(px.bar(ag, x="bucket", y="BDT", title="Receivables aging (days overdue)"), width="stretch")
        bp = pd.DataFrame([{"partner": k, "outstanding_bdt": v["outstanding_bdt"]} for k, v in summ["by_partner"].items()])
        if len(bp):
            c2.plotly_chart(px.bar(bp, x="outstanding_bdt", y="partner", orientation="h", title="Outstanding by partner"), width="stretch")
    with st.expander("➕ Create invoice"):
        f = st.columns(3)
        partner = f[0].text_input("Partner airline"); country = f[1].text_input("Country")
        direction = f[2].selectbox("Direction", ["provided", "received"])
        f2 = st.columns(4)
        stype = f2[0].selectbox("Service", get("/invoices/service-types") or [])
        currency = f2[1].selectbox("Currency", ["USD", "BDT", "EUR"]); rate = f2[2].number_input("Rate to BDT", 0.0, value=110.0)
        vat = f2[3].number_input("VAT %", 0.0, value=0.0)
        li = st.data_editor(pd.DataFrame([{"description": "", "quantity": 1.0, "unit_price": 0.0}]), num_rows="dynamic",
                            width="stretch", key="inv_items")
        if st.button("Create invoice") and partner:
            r = post("/invoices", {"partner": partner, "country": country, "direction": direction, "service_type": stype,
                                   "currency": currency, "exchange_rate": rate, "vat_pct": vat, "items": li.to_dict("records")})
            if r: st.success(f"Created {r['number']}"); st.rerun()
    imp = st.file_uploader("Import invoices CSV", type=["csv"], key="inv_up")
    if imp is not None and st.session_state.get("inv_imp") != (imp.name, imp.size):
        r = call("POST", "/invoices/import", files={"file": (imp.name, imp.getvalue())})
        if r is not None:
            st.session_state["inv_imp"] = (imp.name, imp.size)
            st.success(f"Imported {r.json()['created']} invoices; skipped {len(r.json()['skipped'])}: {r.json()['skipped']}"); st.rerun()
    invs = get("/invoices") or []
    if invs:
        df = pd.DataFrame(invs)
        sf = st.multiselect("Filter status", sorted(df["effective_status"].unique()), default=[])
        view = df[df["effective_status"].isin(sf)] if sf else df
        st.dataframe(view[["id", "number", "partner", "service_type", "currency", "total", "total_bdt", "due_date", "effective_status", "days_overdue"]],
                     width="stretch", height=300)
        a1, a2, a3 = st.columns(3)
        pick = a1.selectbox("Invoice", view["id"].tolist(), format_func=lambda i: df[df["id"] == i]["number"].iloc[0])
        newst = a2.selectbox("Set status", ["submitted_to_accounts", "paid", "pending"])
        if a2.button("Update status"):
            call("PATCH", f"/invoices/{pick}/status", json={"status": newst}); st.rerun()
        if a3.button("Draft payment reminder"):
            st.session_state["rem"] = call("POST", f"/invoices/{pick}/reminder", params={"lang": lang, "use_ai": use_ai})
        if st.session_state.get("rem") is not None:
            st.text_area("Reminder letter", st.session_state["rem"].json()["text"], height=260)
        if st.checkbox("Preview invoice"):
            st.iframe(call("GET", f"/invoices/{pick}/html").text, height=520)

# ------------------------------------------------------------------------------------------ audit
with tab(9):
    st.subheader("Audit trail")
    explain("Every action by every agent and person is logged: who, what, when, how long.")
    s = get("/stats") or {}
    m = st.columns(4)
    m[0].metric("Actions logged", s.get("actions_logged", 0)); m[1].metric("Manual effort replaced", f"{s.get('manual_minutes_estimate', 0)} min")
    m[2].metric("Automated time", f"{s.get('automated_seconds', 0)} s"); m[3].metric("Time reduction", f"{s.get('reduction_pct', 0)}%")
    log = get("/audit", params={"dataset_id": ds_id} if ds_id and st.checkbox("Only the active dataset") else {}) or []
    if log:
        st.dataframe(pd.DataFrame(log)[["ts", "actor", "agent", "action", "dataset_id", "rows", "seconds", "details"]],
                     width="stretch", height=420)
    docs = get("/documents") or []
    if docs:
        with st.expander("Generated documents"):
            st.dataframe(pd.DataFrame(docs), width="stretch")
