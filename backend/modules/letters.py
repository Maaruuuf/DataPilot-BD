"""Module 13: official letter drafting + meeting minutes with action items (DOCX export)."""
import io
import re
from datetime import date
from typing import Dict, List, Optional

from ..core.llm import LLM, LANG_NAME
from ..core.privacy import Tokenizer

LETTER_TYPES = {
    "qa_extension": {
        "label": "QA: license / authorization / certificate extension request",
        "fields": ["staff_name", "emp_id", "document", "doc_no", "expiry_date", "requested_extension", "justification"],
        "to": ("Head of Quality Assurance", "Quality Assurance Department"),
        "subject": "Request for validation / extension of {document} - {staff_name} ({emp_id})",
        "body": ("This is to inform you that the {document} (No. {doc_no}) of {staff_name}, ID {emp_id}, is due to expire on "
                 "{expiry_date}.\n\nIn order to maintain uninterrupted certification support, you are kindly requested to "
                 "validate the document and process an extension{requested_extension_txt}.\n\n{justification}"),
    },
    "visa_extension": {
        "label": "Admin & Protocol: work permit / visa extension (expatriate AME)",
        "fields": ["staff_name", "emp_id", "nationality", "passport_no", "permit_expiry", "visa_expiry", "requested_period"],
        "to": ("Head of Admin & Protocol", "Admin & Protocol Department"),
        "subject": "Work permit / visa extension for expatriate AME - {staff_name}",
        "body": ("The services of {staff_name} ({nationality}, Passport No. {passport_no}), Aircraft Maintenance Engineer "
                 "(ID {emp_id}), are required by the Engineering Department.\n\nHis/her work permit expires on {permit_expiry} "
                 "and visa on {visa_expiry}. Please arrange the extension for {requested_period} and coordinate with the "
                 "relevant authorities."),
    },
    "caab_pass_renewal": {
        "label": "Security: CAAB / D-pass renewal",
        "fields": ["staff_name", "emp_id", "pass_no", "pass_type", "expiry_date"],
        "to": ("Head of Security", "Security Department"),
        "subject": "Renewal of {pass_type} - {staff_name} ({emp_id})",
        "body": ("The {pass_type} (No. {pass_no}) issued to {staff_name}, ID {emp_id}, expires on {expiry_date}. "
                 "You are requested to process its renewal so that access to the airside work area is not interrupted."),
    },
    "training_nomination": {
        "label": "Training nomination (certifying staff)",
        "fields": ["course", "provider", "start_date", "end_date", "nominees", "reason"],
        "to": ("Head of Training / QA", "Quality Assurance Department"),
        "subject": "Nomination for {course}",
        "body": ("The following engineer(s) are nominated for {course} conducted by {provider} from {start_date} to "
                 "{end_date}:\n\n{nominees}\n\n{reason}\n\nYour kind approval and arrangements are requested."),
    },
    "travel_request": {
        "label": "Engineer travel / tour documents",
        "fields": ["staff_name", "emp_id", "destination", "departure_date", "return_date", "purpose", "flight_no"],
        "to": ("Head of Admin", "Administration Department"),
        "subject": "Official travel of {staff_name} to {destination}",
        "body": ("{staff_name} (ID {emp_id}) is required to travel to {destination} from {departure_date} to {return_date} "
                 "(Flight {flight_no}) for: {purpose}.\n\nPlease process the travel documents, tickets and related "
                 "adjustments."),
    },
    "partner_airline_request": {
        "label": "Partner airline: technical support / agreement",
        "fields": ["airline", "aircraft_reg", "issue", "support_requested", "required_by"],
        "to": ("Director Engineering / Technical Services", "Partner Airline"),
        "subject": "Technical support request - {aircraft_reg}",
        "body": ("Under our technical support arrangement we request your assistance for aircraft {aircraft_reg}.\n\n"
                 "Issue: {issue}\nSupport requested: {support_requested}\nRequired by: {required_by}\n\n"
                 "Please confirm availability and the applicable charges."),
    },
    "payment_reminder": {
        "label": "Partner airline: payment reminder",
        "fields": ["airline", "invoice_no", "amount", "currency", "due_date", "days_overdue"],
        "to": ("Finance / Accounts Department", "Partner Airline"),
        "subject": "Reminder: payment of invoice {invoice_no}",
        "body": ("Our records show that invoice {invoice_no} for {currency} {amount}, due on {due_date}, remains unpaid "
                 "({days_overdue} day(s) overdue).\n\nWe kindly request settlement at your earliest convenience."),
    },
    "general": {
        "label": "General official letter",
        "fields": ["subject", "points"],
        "to": ("", ""),
        "subject": "{subject}",
        "body": "{points}",
    },
}

SENDER_DEFAULT = {"name": "Executive, Engineering", "dept": "Engineering Department", "org": "Airline (Demo)"}


class _SafeDict(dict):
    def __missing__(self, key):
        return "[" + key.replace("_", " ") + "]"


def render_template(ltype: str, fields: Dict) -> Dict:
    spec = LETTER_TYPES[ltype]
    f = _SafeDict({k: v for k, v in fields.items() if v not in (None, "")})
    f["requested_extension_txt"] = f" up to {fields['requested_extension']}" if fields.get("requested_extension") else ""
    return {"subject": spec["subject"].format_map(f), "body": spec["body"].format_map(f).strip()}


def draft_letter(ltype: str, fields: Dict, llm: LLM, lang: str = "en", use_ai: bool = True,
                 sender: Optional[Dict] = None, privacy: bool = True) -> Dict:
    if ltype not in LETTER_TYPES:
        raise ValueError(f"Unknown letter type '{ltype}'")
    spec = LETTER_TYPES[ltype]
    base = render_template(ltype, fields)
    subject, body, method = base["subject"], base["body"], "template"
    if use_ai and llm.available:
        tok = Tokenizer()
        safe = tok.mask(str(fields)) if privacy else str(fields)
        system = ("You draft formal official letters for an airline engineering department in Bangladesh. "
                  "Tone: respectful, concise, bureaucratic-professional. Use only the facts provided; if a fact is "
                  "missing keep a [placeholder] instead of inventing it. Do not add salutation or signature. "
                  f"Return JSON {{\"subject\": str, \"body\": str}}. Language: {LANG_NAME.get(lang, 'English')}. "
                  "Copy placeholders like <PHONE_1> unchanged.")
        data = llm.json(system, f"Letter type: {spec['label']}\nFacts: {safe}\nDraft to improve:\n{body}")
        if isinstance(data, dict) and data.get("body"):
            subject = tok.unmask(str(data.get("subject") or subject)) if privacy else str(data.get("subject") or subject)
            body = tok.unmask(str(data["body"])) if privacy else str(data["body"])
            method = "llm"
    s = {**SENDER_DEFAULT, **(sender or {})}
    to_title, to_org = spec["to"]
    if fields.get("to_title"):
        to_title = fields["to_title"]
    if fields.get("airline") and ltype in ("partner_airline_request", "payment_reminder"):
        to_org = fields["airline"]
    ref = fields.get("ref_no") or f"ENG/{date.today().year}/___"
    text = _compose(ref, str(fields.get("date") or date.today().isoformat()), to_title, to_org, subject, body, s, lang)
    return {"subject": subject, "body": body, "text": text, "method": method, "ref_no": ref}


def _compose(ref, dt, to_title, to_org, subject, body, sender, lang) -> str:
    dear = "মহোদয়," if lang == "bn" else "Dear Sir/Madam,"
    regards = "বিনীত," if lang == "bn" else "Yours faithfully,"
    to_lines = "\n".join(x for x in (to_title, to_org) if x)
    return (f"Ref: {ref}\nDate: {dt}\n\nTo,\n{to_lines}\n\nSubject: {subject}\n\n{dear}\n\n{body}\n\n"
            f"{regards}\n\n\n{sender['name']}\n{sender['dept']}\n{sender['org']}")


def letter_docx(text: str, lang: str = "en") -> bytes:
    from docx import Document
    from docx.shared import Pt
    from docx.oxml.ns import qn
    doc = Document()
    font = "Nirmala UI" if lang == "bn" else "Times New Roman"
    st = doc.styles["Normal"]
    st.font.name, st.font.size = font, Pt(12)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), font)
    for block in text.split("\n"):
        p = doc.add_paragraph()
        run = p.add_run(block)
        if block.startswith("Subject:"):
            run.bold = True
        p.paragraph_format.space_after = Pt(2 if block.strip() else 8)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------------------- meeting minutes
ACTION_RE = re.compile(r"(?i)\b(will|to be done|shall|must|should|needs? to|action|follow[- ]?up|responsible)\b")
DEADLINE_RE = re.compile(r"(?i)\b(?:by|before|until|within)\s+([A-Za-z0-9 ,/\-]{3,30}?)(?=[.;,]|$)")
OWNER_RE = re.compile(r"^\s*(?:[-*•\d.)]+\s*)?(?:action[:\-]\s*)?([A-Z][\w.]*(?:\s[A-Z][\w.]*){0,2})\s+(?:will|shall|to|must|should|needs? to)\b")


def _minutes_fallback(notes: str) -> Dict:
    actions, discussion, decisions = [], [], []
    for line in [l.strip(" -*•\t") for l in notes.splitlines() if l.strip()]:
        low = line.lower()
        if low.startswith(("decision", "decided", "agreed", "resolved")):
            decisions.append(re.sub(r"(?i)^(decision|decided|agreed|resolved)[:\- ]*", "", line))
        elif ACTION_RE.search(line):
            om = OWNER_RE.match(line)
            dm = DEADLINE_RE.search(line)
            actions.append({"action": re.sub(r"(?i)^action[:\- ]*", "", line), "owner": om[1] if om else "TBD",
                            "deadline": dm[1].strip() if dm else "TBD", "status": "open"})
        else:
            discussion.append(line)
    return {"discussion": discussion, "decisions": decisions, "actions": actions}


def draft_minutes(notes: str, meta: Dict, llm: LLM, lang: str = "en", privacy: bool = True) -> Dict:
    data, method = None, "rules"
    if llm.available:
        tok = Tokenizer()
        safe = tok.mask(notes) if privacy else notes
        system = ("You turn rough meeting notes into formal minutes. Use only what the notes say; if an owner or deadline "
                  "is not stated use \"TBD\". Return JSON {\"discussion\":[str],\"decisions\":[str],"
                  "\"actions\":[{\"action\":str,\"owner\":str,\"deadline\":str}]}. "
                  f"Language: {LANG_NAME.get(lang, 'English')}. Copy placeholders like <PHONE_1> unchanged.")
        res = llm.json(system, safe)
        if isinstance(res, dict) and (res.get("actions") is not None):
            data = tok.unmask(res) if privacy else res
            method = "llm"
    if data is None:
        data = _minutes_fallback(notes)
    actions = [{"action": a.get("action", ""), "owner": a.get("owner") or "TBD",
                "deadline": a.get("deadline") or "TBD", "status": "open"} for a in data.get("actions", [])]
    out = {"title": meta.get("title") or "Meeting Minutes", "date": meta.get("date") or date.today().isoformat(),
           "venue": meta.get("venue", ""), "attendees": meta.get("attendees", []),
           "discussion": data.get("discussion", []), "decisions": data.get("decisions", []),
           "actions": actions, "method": method}
    out["text"] = minutes_text(out, lang)
    return out


def minutes_text(m: Dict, lang: str = "en") -> str:
    h = {"en": ("MINUTES OF MEETING", "Date", "Venue", "Attendees", "Discussion", "Decisions", "Action Items",
                "Action", "Owner", "Deadline"),
         "bn": ("সভার কার্যবিবরণী", "তারিখ", "স্থান", "উপস্থিতি", "আলোচনা", "সিদ্ধান্ত", "করণীয়",
                "কাজ", "দায়িত্বপ্রাপ্ত", "সময়সীমা")}[lang if lang in ("en", "bn") else "en"]
    lines = [h[0], m["title"], f"{h[1]}: {m['date']}", f"{h[2]}: {m['venue']}",
             f"{h[3]}: {', '.join(m['attendees'])}", "", h[4] + ":"]
    lines += [f"  {i}. {d}" for i, d in enumerate(m["discussion"], 1)]
    lines += ["", h[5] + ":"] + [f"  {i}. {d}" for i, d in enumerate(m["decisions"], 1)]
    lines += ["", h[6] + ":"]
    lines += [f"  {i}. {a['action']}  [{h[8]}: {a['owner']} | {h[9]}: {a['deadline']}]" for i, a in enumerate(m["actions"], 1)]
    return "\n".join(lines)
