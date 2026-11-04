"""Correction Agent: proposes fixes with confidence scores. A human approves/rejects (Feature 5)."""
import re
from typing import Dict, List
import pandas as pd

from ..core.dataio import is_null
from ..core.llm import LLM, LANG_NAME
from ..core.privacy import pii_columns, mask_df
from ..core.localization import (normalize_bd_mobile, to_en_digits, to_number, parse_date_flex,
                                 normalize_district, has_bn_digits)
from .validation import canonical_groups, EMAIL_RE

DELETE_MARK = "__DELETE_ROW__"


def _c(row, col, old, new, conf, reason, source="rule"):
    return {"row_index": int(row), "column": col, "old_value": str(old), "new_value": str(new),
            "confidence": round(conf, 2), "reason": reason, "source": source}


def suggest_corrections(df: pd.DataFrame, issues: List[Dict], profile: Dict, llm: LLM = None,
                        use_ai: bool = False, lang: str = "en", privacy: bool = True) -> List[Dict]:
    kinds = {c["name"]: c["kind"] for c in profile["column_info"]}
    out, handled = [], set()

    # canonical spellings for categorical columns
    canon = {}
    for col, kind in kinds.items():
        if kind in ("category", "boolean"):
            canon[col] = canonical_groups([str(v).strip() for v in df[col] if not is_null(v)])

    for it in issues:
        r, col, rule, val = it["row"], it["column"], it["rule"], df[it["column"]].iloc[it["row"]]
        key = (r, col)
        if key in handled and rule != "duplicate_rows":
            continue
        fix = None
        if rule == "trim":
            fix = _c(r, col, val, re.sub(r"\s+", " ", str(val).strip()), 0.99, "Removed extra spaces")
        elif rule == "bd_mobile" and it["fixable"]:
            norm = normalize_bd_mobile(val)
            if norm:
                reason = "Converted Bangla digits to English and standardised to +880 format" \
                    if has_bn_digits(val) else "Standardised to +880 format"
                fix = _c(r, col, val, norm, 0.97, reason)
        elif rule == "bd_nid" and it["fixable"]:
            fix = _c(r, col, val, to_en_digits(val).strip(), 0.97, "Converted Bangla digits to English")
        elif rule == "date" and it["fixable"]:
            d, _ = parse_date_flex(val)
            if d:
                fix = _c(r, col, val, d.isoformat(), 0.9, "Reformatted to ISO yyyy-mm-dd (assumed day-first, as is standard in Bangladesh)")
        elif rule in ("integer", "float") and it["fixable"]:
            n = to_number(val)
            if n is not None:
                fix = _c(r, col, val, str(int(n)) if rule == "integer" else str(n), 0.93,
                         "Removed currency symbols/commas/Bangla digits")
        elif rule == "bd_district" and it["fixable"]:
            fix = _c(r, col, val, normalize_district(val), 0.9, "Matched official district spelling")
        elif rule == "consistent_naming":
            target = canon.get(col, {}).get(str(val).strip())
            if target and target != str(val).strip():
                case_only = target.lower() == str(val).strip().lower()
                fix = _c(r, col, val, target, 0.97 if case_only else 0.88,
                         "Unified capitalisation" if case_only else "Unified with the most common spelling")
        elif rule == "email":
            cleaned = re.sub(r"\s+", "", str(val)).lower().replace("@@", "@")
            if cleaned != str(val) and EMAIL_RE.match(cleaned):
                fix = _c(r, col, val, cleaned, 0.8, "Removed spaces / double @ and lower-cased")
        elif rule == "duplicate_rows":
            fix = _c(r, col, it["message"], DELETE_MARK, 0.7, "Remove duplicate record (keeps the first occurrence)")
        if fix:
            out.append(fix)
            handled.add(key)

    if use_ai and llm and llm.available:
        out.extend(_ai_fixes(df, issues, handled, llm, lang, privacy))
    return out


def _ai_fixes(df, issues, handled, llm, lang, privacy) -> List[Dict]:
    """Ask the LLM for fixes on the issues deterministic logic could not handle (non-PII columns only)."""
    pii = set(pii_columns(df.columns)) if privacy else set()
    todo = [i for i in issues if (i["row"], i["column"]) not in handled and i["column"] not in pii
            and i["rule"] in ("allowed", "regex", "bd_district", "ai_check", "date", "integer", "float", "email")][:20]
    if not todo:
        return []
    payload = []
    for i in todo:
        ctx = mask_df(df.iloc[[i["row"]]]).iloc[0].to_dict() if privacy else df.iloc[i["row"]].to_dict()
        payload.append({"row": i["row"], "column": i["column"], "value": i["value"], "problem": i["message"],
                        "record": ctx})
    system = ("You fix data-entry errors. For each item propose a corrected value ONLY if you are reasonably sure; "
              "otherwise omit it. Return {\"fixes\":[{\"row\":int,\"column\":str,\"new_value\":str,"
              "\"confidence\":0-1,\"reason\":str}]}. Reason in " + LANG_NAME.get(lang, "English") + ".")
    data = llm.json(system, str(payload))
    res = []
    if isinstance(data, dict):
        for f in data.get("fixes", []):
            try:
                r, col = int(f["row"]), f["column"]
                if 0 <= r < len(df) and col in df.columns and str(f["new_value"]) != str(df[col].iloc[r]):
                    res.append(_c(r, col, df[col].iloc[r], f["new_value"], min(float(f.get("confidence", 0.6)), 0.85),
                                  str(f.get("reason", "AI suggestion"))[:300], "ai"))
            except (KeyError, ValueError, TypeError):
                continue
    return res


def apply_corrections(df: pd.DataFrame, corrections: List[Dict]) -> pd.DataFrame:
    out = df.copy()
    drop = []
    for c in corrections:
        if c["new_value"] == DELETE_MARK:
            drop.append(c["row_index"])
        elif c["column"] in out.columns and 0 <= c["row_index"] < len(out):
            out.iat[c["row_index"], out.columns.get_loc(c["column"])] = c["new_value"]
    if drop:
        out = out.drop(index=drop)
    return out.reset_index(drop=True)
