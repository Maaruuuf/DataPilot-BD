"""Extraction Agent: messy text -> validated structured rows (Feature 6)."""
import re
from typing import Dict, List, Optional

from ..core.llm import LLM, LANG_NAME
from ..core.privacy import Tokenizer
from ..core.localization import (normalize_bd_mobile, to_number, parse_date_flex, to_en_digits,
                                 valid_bd_nid, nid_not_applicable)
from .intake import EMAIL_RE

# field -> (type, required)
SCHEMAS: Dict[str, Dict[str, tuple]] = {
    "invoice": {"invoice_no": ("str", True), "partner": ("str", True), "invoice_date": ("date", True),
                "service_description": ("str", False), "quantity": ("number", False),
                "unit_price": ("number", False), "currency": ("str", False), "total_amount": ("number", True)},
    "travel_request": {"employee_name": ("str", True), "emp_id": ("str", False), "destination": ("str", True),
                       "departure_date": ("date", True), "return_date": ("date", False), "purpose": ("str", False),
                       "flight_no": ("str", False), "passport_no": ("str", False)},
    "staff_joining": {"full_name": ("str", True), "designation": ("str", True), "join_date": ("date", True),
                      "mobile": ("mobile", True), "email": ("email", False), "nid": ("nid", False),
                      "bank_account": ("str", False), "department": ("str", False)},
    "order": {"customer_name": ("str", True), "mobile": ("mobile", True), "address": ("str", False),
              "item": ("str", True), "quantity": ("number", True), "price": ("number", False)},
}


def schema_for(name: str, custom_fields: Optional[List[str]] = None) -> Dict[str, tuple]:
    if name in SCHEMAS:
        return SCHEMAS[name]
    return {f: ("str", False) for f in (custom_fields or [])}


def coerce(value, ftype: str):
    """Return (clean_value, problem|None)."""
    if value is None or str(value).strip().lower() in ("", "null", "none", "n/a"):
        return None, None
    s = str(value).strip()
    if ftype == "date":
        d, _ = parse_date_flex(s)
        return (d.isoformat(), None) if d else (s, "invalid date")
    if ftype == "number":
        n = to_number(s)
        return (n, None) if n is not None else (s, "not a number")
    if ftype == "mobile":
        n = normalize_bd_mobile(s)
        return (n, None) if n else (s, "invalid BD mobile")
    if ftype == "email":
        s = s.replace(" ", "").lower()
        return (s, None) if EMAIL_RE.match(s) else (s, "invalid email")
    if ftype == "nid":
        s = to_en_digits(s)
        return (s, None) if valid_bd_nid(s) or nid_not_applicable(s) else (s, "NID must be 10/13/17 digits")
    return s, None


def _alias(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _rule_based(text: str, fields: List[str]) -> List[Dict]:
    """Offline parser: understands 'Key: value' lines; records are split by blank lines or '---'."""
    blocks = [b for b in re.split(r"\n\s*\n|^-{3,}\s*$", text, flags=re.M) if b.strip()]
    rows = []
    for b in blocks:
        row = {f: None for f in fields}
        for line in b.splitlines():
            m = re.match(r"\s*([^:=]{2,40})\s*[:=]\s*(.+)", line)
            if not m:
                continue
            k = _alias(m[1])
            for f in fields:
                if k == f or k in f or f in k:
                    row[f] = m[2].strip()
                    break
        if any(v for v in row.values()):
            rows.append(row)
    return rows


def extract(text: str, schema_name: str, llm: LLM, custom_fields: Optional[List[str]] = None,
            lang: str = "en", privacy: bool = True) -> Dict:
    schema = schema_for(schema_name, custom_fields)
    fields = list(schema)
    if not fields:
        return {"rows": [], "warnings": ["No fields defined"], "method": "none", "schema": {}}
    tok = Tokenizer()
    rows, method = [], "rules"
    if llm.available:
        safe_text = tok.mask(text) if privacy else text
        system = (f"You are a data-entry clerk. Extract every record from the text into JSON "
                  f"{{\"rows\":[{{...}}]}}. Use EXACTLY these keys: {fields}. Use null when a value is absent; never "
                  f"guess. Dates as YYYY-MM-DD (Bangladesh text is day-first). Numbers as plain numbers. "
                  f"Convert Bangla digits to English digits. Placeholders like <PHONE_1> must be copied unchanged. "
                  f"Field values stay in the language of the source text.")
        data = llm.json(system, safe_text)
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            rows = [{f: r.get(f) for f in fields} for r in data["rows"] if isinstance(r, dict)]
            rows = tok.unmask(rows) if privacy else rows
            method = "llm"
    if not rows:
        rows = _rule_based(text, fields)
        method = "rules"
    clean_rows, warnings = [], []
    for n, r in enumerate(rows, 1):
        out = {}
        for f, (ftype, required) in schema.items():
            val, prob = coerce(r.get(f), ftype)
            out[f] = val
            if prob:
                warnings.append(f"Record {n}: {f} - {prob} ('{val}')")
            elif required and val is None:
                warnings.append(f"Record {n}: required field '{f}' is missing")
        clean_rows.append(out)
    return {"rows": clean_rows, "warnings": warnings, "method": method,
            "schema": {k: {"type": v[0], "required": v[1]} for k, v in schema.items()}}
