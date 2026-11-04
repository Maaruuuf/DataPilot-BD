"""Validation Agent: hybrid rule + AI validation engine (Features 3 & 4) and the Data Quality Score."""
import difflib
import re
from typing import Dict, List, Optional
import pandas as pd

from ..core.dataio import is_null
from ..core.llm import LLM, LANG_NAME
from ..core.privacy import mask_df
from ..core.localization import (normalize_bd_mobile, valid_bd_nid, nid_not_applicable, to_number,
                                 parse_date_flex, normalize_district, has_bn_digits)
from .intake import EMAIL_RE, tokens

SEV = ("error", "warning")
ABBREV = {"sr": "senior", "jr": "junior", "asst": "assistant", "mgr": "manager", "engr": "engineer",
          "tech": "technician", "dy": "deputy", "eng": "engineer", "dept": "department"}

RULE_TYPES = {
    "not_null": "column must not be empty",
    "unique": "values must be unique",
    "integer": "whole numbers only",
    "float": "numbers only",
    "date": "valid date (ISO yyyy-mm-dd)",
    "email": "valid email address",
    "bd_mobile": "valid Bangladeshi mobile number",
    "bd_nid": "valid Bangladeshi NID (10/13/17 digits)",
    "bd_district": "valid Bangladeshi district/division",
    "range": "numeric value between min and max",
    "regex": "must match a regular expression",
    "allowed": "must be one of a list of values",
    "date_order": "start date must not be after end date",
    "implies": "if one column has a value then another column must satisfy a condition",
    "outlier": "statistical outlier (IQR)",
    "consistent_naming": "same category written in different ways",
    "duplicate_rows": "duplicate records",
    "trim": "leading/trailing/double spaces",
}
CONSISTENCY_RULES = {"consistent_naming"}
UNIQUENESS_RULES = {"unique", "duplicate_rows"}
COMPLETENESS_RULES = {"not_null"}


def _issue(df, row, col, rule, sev, msg, value="", fixable=False, source="rule"):
    rec = str(df.iloc[row, 0]) if len(df.columns) else ""
    return {"row": int(row), "row_number": int(row) + 2, "record": rec, "column": col, "rule": rule,
            "severity": sev, "message": msg, "value": str(value), "fixable": fixable, "source": source}


# ---------------------------------------------------------------------------------- individual checks
def check_not_null(df, r):
    c, sev = r["column"], r.get("severity", "error")
    return [_issue(df, i, c, "not_null", sev, f"{c} is missing") for i, v in enumerate(df[c]) if is_null(v)]


def check_unique(df, r):
    c, sev = r["column"], r.get("severity", "error")
    seen, out = {}, []
    for i, v in enumerate(df[c]):
        if is_null(v) or nid_not_applicable(v):
            continue
        key = str(v).strip().lower()
        if key in seen:
            out.append(_issue(df, i, c, "unique", sev,
                              f"Duplicate {c} (same as row {seen[key] + 2})", v))
        else:
            seen[key] = i
    return out


def check_number(df, r, integer):
    c, sev = r["column"], r.get("severity", "error")
    pat = re.compile(r"^-?\d+$" if integer else r"^-?\d+(\.\d+)?$")
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v):
            continue
        s = str(v).strip()
        if pat.match(s):
            continue
        n = to_number(s)
        if n is not None and (not integer or float(n).is_integer()):
            out.append(_issue(df, i, c, r["type"], "warning",
                              f"Non-standard number format '{s}'", s, fixable=True))
        else:
            out.append(_issue(df, i, c, r["type"], sev,
                              f"'{s}' is not a valid {'whole number' if integer else 'number'}", s))
    return out


def check_date(df, r):
    c, sev = r["column"], r.get("severity", "error")
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v):
            continue
        d, kind = parse_date_flex(v)
        if d is None:
            out.append(_issue(df, i, c, "date", sev, f"'{v}' is not a valid date", v))
        elif kind != "iso":
            out.append(_issue(df, i, c, "date", "warning",
                              f"Date '{v}' is not in ISO format (yyyy-mm-dd); read as day-first {d.isoformat()}",
                              v, fixable=True))
    return out


def check_email(df, r):
    c, sev = r["column"], r.get("severity", "error")
    return [_issue(df, i, c, "email", sev, f"'{v}' is not a valid email", v, fixable=bool(str(v) != str(v).strip()))
            for i, v in enumerate(df[c]) if not is_null(v) and not EMAIL_RE.match(str(v).strip())]


def check_bd_mobile(df, r):
    c, sev = r["column"], r.get("severity", "error")
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v):
            continue
        norm = normalize_bd_mobile(v)
        s = str(v).strip()
        if norm is None:
            out.append(_issue(df, i, c, "bd_mobile", sev, f"'{s}' is not a valid Bangladeshi mobile number", s))
        elif s != norm:
            why = "Bangla digits" if has_bn_digits(s) else "non-standard format"
            out.append(_issue(df, i, c, "bd_mobile", "warning", f"Mobile uses {why}; standard form is {norm}",
                              s, fixable=True))
    return out


def check_bd_nid(df, r):
    c, sev = r["column"], r.get("severity", "error")
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v) or nid_not_applicable(v):
            continue
        s = str(v).strip()
        if not valid_bd_nid(s):
            out.append(_issue(df, i, c, "bd_nid", sev, f"NID '{s}' must have 10, 13 or 17 digits", s))
        elif has_bn_digits(s):
            out.append(_issue(df, i, c, "bd_nid", "warning", "NID uses Bangla digits", s, fixable=True))
    return out


def check_bd_district(df, r):
    c, sev = r["column"], r.get("severity", "warning")
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v):
            continue
        norm = normalize_district(v)
        if norm is None:
            out.append(_issue(df, i, c, "bd_district", sev, f"'{v}' is not a recognised district/division", v))
        elif norm != str(v).strip():
            out.append(_issue(df, i, c, "bd_district", "warning",
                              f"Spelling differs from official name '{norm}'", v, fixable=True))
    return out


def check_range(df, r):
    c, sev = r["column"], r.get("severity", "error")
    lo, hi = r.get("min"), r.get("max")
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v):
            continue
        n = to_number(v)
        if n is None:
            continue  # type rules report non-numeric values
        if (lo is not None and n < lo) or (hi is not None and n > hi):
            bound = f"{lo if lo is not None else '-inf'} to {hi if hi is not None else 'inf'}"
            out.append(_issue(df, i, c, "range", sev, f"{c}={v} is outside the allowed range ({bound})", v))
    return out


def check_regex(df, r):
    c, sev = r["column"], r.get("severity", "error")
    try:
        pat = re.compile(r["pattern"])
    except re.error:
        return []
    return [_issue(df, i, c, "regex", sev, f"'{v}' does not match the required pattern", v)
            for i, v in enumerate(df[c]) if not is_null(v) and not pat.fullmatch(str(v).strip())]


def check_allowed(df, r):
    c, sev = r["column"], r.get("severity", "error")
    allowed = {str(a).strip().lower() for a in r.get("values", [])}
    return [_issue(df, i, c, "allowed", sev, f"'{v}' is not an allowed value", v)
            for i, v in enumerate(df[c]) if not is_null(v) and str(v).strip().lower() not in allowed]


def check_date_order(df, r):
    a, b, sev = r["start"], r["end"], r.get("severity", "error")
    out = []
    for i in range(len(df)):
        d1, d2 = parse_date_flex(df[a].iloc[i])[0], parse_date_flex(df[b].iloc[i])[0]
        if d1 and d2 and d1 > d2:
            out.append(_issue(df, i, b, "date_order", sev, f"{b} ({d2}) is before {a} ({d1})", df[b].iloc[i]))
    return out


_OPS = {
    "eq": lambda a, b: str(a).strip().lower() == str(b).strip().lower(),
    "ne": lambda a, b: str(a).strip().lower() != str(b).strip().lower(),
    "gt": lambda a, b: (to_number(a) or 0) > float(b),
    "gte": lambda a, b: (to_number(a) or 0) >= float(b),
    "lt": lambda a, b: (to_number(a) or 0) < float(b),
    "lte": lambda a, b: (to_number(a) or 0) <= float(b),
    "not_null": lambda a, b: not is_null(a),
    "is_null": lambda a, b: is_null(a),
}


def check_implies(df, r):
    wc, wo, wv = r["when_column"], r.get("when_op", "eq"), r.get("when_value")
    tc, to, tv = r["then_column"], r.get("then_op", "eq"), r.get("then_value")
    sev = r.get("severity", "error")
    out = []
    for i in range(len(df)):
        try:
            if _OPS[wo](df[wc].iloc[i], wv) and not _OPS[to](df[tc].iloc[i], tv):
                out.append(_issue(df, i, tc, "implies", sev,
                                  r.get("message") or f"When {wc} {wo} {wv}, {tc} must be {to} {tv if tv is not None else ''}".strip(),
                                  df[tc].iloc[i]))
        except (ValueError, TypeError):
            continue
    return out


def check_outlier(df, r):
    c, k, sev = r["column"], float(r.get("k", 3.0)), r.get("severity", "warning")
    nums = [(i, to_number(v)) for i, v in enumerate(df[c]) if not is_null(v) and to_number(v) is not None]
    if len(nums) < 8:
        return []
    if sum(1 for _, n in nums if n == 0) / len(nums) >= 0.3:  # zero-inflated column: judge the non-zero values
        nums = [(i, n) for i, n in nums if n != 0]
        if len(nums) < 8:
            return []
    s = pd.Series([n for _, n in nums])
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return []
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return [_issue(df, i, c, "outlier", sev, f"{c}={df[c].iloc[i]} is an extreme outlier (expected {lo:.0f}-{hi:.0f})",
                   df[c].iloc[i]) for i, n in nums if n < lo or n > hi]


def _norm_label(v: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", str(v).lower()).split()
    return " ".join(ABBREV.get(w, w) for w in words)



def _is_typo_pair(a: str, b: str) -> bool:
    """True only for genuine spelling variants, e.g. 'line maintenence' vs 'line maintenance'.
    'technician b1' vs 'technician b2' or 'cat a' vs 'cat b' are distinct values, not typos."""
    if len(a) < 8 or re.search(r"\d", a) or re.search(r"\d", b):
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() < 0.9:
        return False
    diff = set(a.split()) ^ set(b.split())
    return bool(diff) and all(len(t) >= 5 for t in diff)


def canonical_groups(values: List[str]) -> Dict[str, str]:
    """Map every variant to the most frequent spelling of the same normalised label."""
    groups: Dict[str, Dict[str, int]] = {}
    for v in values:
        groups.setdefault(_norm_label(v), {})
        groups[_norm_label(v)][v] = groups[_norm_label(v)].get(v, 0) + 1
    mapping = {}
    keys = list(groups)
    # merge very close normalised labels (typos) - but NEVER labels that differ in digits or short codes
    parent = {k: k for k in keys}
    for a in keys:
        for b in keys:
            if a < b and _is_typo_pair(a, b):
                parent[b] = parent[a]
    merged: Dict[str, Dict[str, int]] = {}
    for k in keys:
        merged.setdefault(parent[k], {})
        for v, n in groups[k].items():
            merged[parent[k]][v] = merged[parent[k]].get(v, 0) + n
    for variants in merged.values():
        canon = sorted(variants.items(), key=lambda x: (-x[1], x[0]))[0][0]
        for v in variants:
            mapping[v] = canon
    return mapping


def check_consistent_naming(df, r):
    c, sev = r["column"], r.get("severity", "warning")
    vals = [str(v).strip() for v in df[c] if not is_null(v)]
    mapping = canonical_groups(vals)
    out = []
    for i, v in enumerate(df[c]):
        if is_null(v):
            continue
        canon = mapping.get(str(v).strip())
        if canon and canon != str(v).strip():
            out.append(_issue(df, i, c, "consistent_naming", sev,
                              f"'{v}' is written differently from '{canon}'", v, fixable=True))
    return out


def check_duplicate_rows(df, r):
    ignore = set(r.get("ignore_columns", []))
    cols = [c for c in df.columns if c not in ignore]
    if not cols:
        return []
    dup = df.duplicated(subset=cols, keep="first")
    first = {}
    out = []
    for i in range(len(df)):
        key = tuple(df.iloc[i][cols])
        if dup.iloc[i]:
            out.append(_issue(df, i, df.columns[0], "duplicate_rows", "error",
                              f"Row duplicates row {first[key] + 2} (all fields identical except IDs)",
                              df.iloc[i, 0], fixable=True))
        else:
            first[key] = i
    return out


def check_trim(df, r):
    c = r["column"]
    return [_issue(df, i, c, "trim", "warning", "Extra spaces", repr(v), fixable=True)
            for i, v in enumerate(df[c]) if isinstance(v, str) and v != re.sub(r"\s+", " ", v.strip())]


CHECKS = {
    "not_null": check_not_null, "unique": check_unique,
    "integer": lambda d, r: check_number(d, r, True), "float": lambda d, r: check_number(d, r, False),
    "date": check_date, "email": check_email, "bd_mobile": check_bd_mobile, "bd_nid": check_bd_nid,
    "bd_district": check_bd_district, "range": check_range, "regex": check_regex, "allowed": check_allowed,
    "date_order": check_date_order, "implies": check_implies, "outlier": check_outlier,
    "consistent_naming": check_consistent_naming, "duplicate_rows": check_duplicate_rows, "trim": check_trim,
}

NONNEG_HINTS = ("leave", "balance", "hours", "sectors", "qty", "quantity", "amount", "price", "salary", "total")


# ---------------------------------------------------------------------------------- rule inference
def infer_rules(df: pd.DataFrame, profile: Dict) -> List[Dict]:
    rules: List[Dict] = []
    cols = {c["name"]: c for c in profile["column_info"]}
    id_cols = [n for n, c in cols.items() if c["kind"] == "id"]
    for name, c in cols.items():
        kind, toks = c["kind"], tokens(name)
        if c["null_pct"] <= 15 and c["null_pct"] < 100:
            rules.append({"type": "not_null", "column": name, "severity": "error"})
        rules.append({"type": "trim", "column": name})
        if kind == "email":
            rules.append({"type": "email", "column": name})
        elif kind == "phone":
            rules.append({"type": "bd_mobile", "column": name})
        elif kind == "nid":
            rules.append({"type": "bd_nid", "column": name})
        elif kind == "date":
            rules.append({"type": "date", "column": name})
        elif kind == "integer":
            rules.append({"type": "integer", "column": name})
        elif kind == "float":
            rules.append({"type": "float", "column": name})
        if kind in ("integer", "float"):
            if any(h in toks for h in NONNEG_HINTS):
                rules.append({"type": "range", "column": name, "min": 0})
            if "age" in toks:
                rules.append({"type": "range", "column": name, "min": 15, "max": 75, "severity": "warning"})
            rules.append({"type": "outlier", "column": name, "k": 3.0})
        if kind == "id":
            rules.append({"type": "unique", "column": name, "severity": "error"})
        if kind in ("category", "boolean"):
            rules.append({"type": "consistent_naming", "column": name})
        if any(t in toks for t in ("district", "division")):
            rules.append({"type": "bd_district", "column": name})
    # nid uniqueness
    for n, c in cols.items():
        if c["kind"] == "nid":
            rules.append({"type": "unique", "column": n, "severity": "error"})
    # date pairs: issue->expiry, start->end, from->to
    for a in list(cols):
        for x, y in (("issue", "expiry"), ("issue", "expire"), ("start", "end"), ("from", "to")):
            if x in a.lower():
                b = a.lower().replace(x, y)
                match = next((n for n in cols if n.lower() == b), None)
                if match and cols[a]["kind"] == "date" and cols[match]["kind"] == "date":
                    rules.append({"type": "date_order", "start": a, "end": match})
    # aviation pack: non-riding staff must not have flying hours
    lower = {n.lower(): n for n in cols}
    flag = next((o for k, o in lower.items() if k in ("riding_engineer", "is_riding_engineer")), None)
    for n in cols:
        if "flying_hours" in n.lower() and flag:
            rules.append({"type": "implies", "when_column": flag, "when_op": "ne", "when_value": "Y",
                          "then_column": n, "then_op": "lte", "then_value": 0, "severity": "error",
                          "message": f"{n} is recorded but {flag} is not 'Y' (flying allowance cannot be paid)"})
    rules.append({"type": "duplicate_rows", "ignore_columns": id_cols})
    return rules


def sanitize_rules(rules: list, columns) -> List[Dict]:
    """Drop rules referencing unknown columns / types (protects against hallucinated LLM rules)."""
    good = []
    colset = set(columns)
    for r in rules or []:
        if not isinstance(r, dict) or r.get("type") not in CHECKS:
            continue
        refs = [r.get(k) for k in ("column", "start", "end", "when_column", "then_column") if k in r]
        if r["type"] == "duplicate_rows":
            good.append(r)
            continue
        if refs and all(x in colset for x in refs):
            if r.get("severity") not in SEV:
                r.pop("severity", None)
            good.append(r)
    return good


# ---------------------------------------------------------------------------------- AI validation
def ai_checks(df: pd.DataFrame, profile: Dict, llm: LLM, lang: str = "en", max_rows: int = 60) -> List[Dict]:
    """Semantic checks a regex cannot do (name/email mismatch, implausible combinations...)."""
    if not llm.available:
        return []
    sample = mask_df(df.head(max_rows))
    cols = ", ".join(f"{c['name']}({c['kind']})" for c in profile["column_info"])
    rows = sample.reset_index().to_dict("records")
    system = ("You are a meticulous data auditor for an airline engineering department in Bangladesh. "
              "Find data problems that simple format rules would miss: implausible values, columns that contradict "
              "each other, names/emails that do not match, impossible dates, inconsistent spellings. "
              "Do NOT report masked values (containing ***) or plain missing values. "
              "Return JSON: {\"issues\":[{\"row\":<index value>,\"column\":<name>,\"message\":<short, in "
              f"{LANG_NAME.get(lang, 'English')}>,\"severity\":\"error|warning\"}}]}} with at most 25 issues.")
    data = llm.json(system, f"Columns: {cols}\nRows (index = 'index' key):\n{rows}")
    out = []
    if isinstance(data, dict):
        for it in data.get("issues", [])[:25]:
            try:
                r = int(it["row"])
                c = it["column"]
                if 0 <= r < len(df) and c in df.columns:
                    out.append(_issue(df, r, c, "ai_check", it.get("severity") if it.get("severity") in SEV else "warning",
                                      str(it.get("message", ""))[:300], df[c].iloc[r], source="ai"))
            except (KeyError, ValueError, TypeError):
                continue
    return out


# ---------------------------------------------------------------------------------- natural-language rules
def nl_to_rules(text: str, df: pd.DataFrame, profile: Dict, llm: LLM) -> Dict:
    """'phone must be a valid BD number, age between 18 and 60' -> executable rule JSON."""
    cols = [c["name"] for c in profile["column_info"]]
    rules: List[Dict] = []
    method = "llm"
    if llm.available:
        system = ("Convert the user's validation requirements into JSON rules. Output {\"rules\":[...]}. "
                  f"Allowed rule types and meaning: {RULE_TYPES}. "
                  "Fields: type, column (must be one of the dataset columns), severity (error|warning), plus "
                  "min/max for range, pattern for regex, values (list) for allowed, start/end for date_order, "
                  "when_column/when_op/when_value/then_column/then_op/then_value for implies "
                  "(ops: eq, ne, gt, gte, lt, lte, not_null, is_null). Use only the dataset columns given.")
        data = llm.json(system, f"Dataset columns: {cols}\nRequirements: {text}")
        if isinstance(data, dict):
            rules = sanitize_rules(data.get("rules", []), cols)
    if not rules:
        method = "keywords"
        rules = _nl_fallback(text, cols)
    return {"rules": rules, "method": method}


def _find_col(fragment: str, cols: List[str]) -> Optional[str]:
    frag = re.sub(r"[^a-z0-9]+", " ", fragment.lower()).strip()
    best, score = None, 0.0
    for c in cols:
        cn = re.sub(r"[^a-z0-9]+", " ", c.lower()).strip()
        if cn and (cn in frag or frag in cn):
            s = len(cn)
        else:
            s = difflib.SequenceMatcher(None, cn, frag).ratio() * 10 if frag else 0
            s = s if s > 7.5 else 0
        if s > score:
            best, score = c, s
    return best


def _nl_fallback(text: str, cols: List[str]) -> List[Dict]:
    """Small offline parser used when no LLM is available."""
    rules = []
    for part in re.split(r"[,;\n]| and (?=[a-z_ ]+ (?:must|should|between|is))", text, flags=re.I):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        col = _find_col(re.split(r"\b(must|should|between|is|has|need)\b", low)[0], cols)
        if not col:
            continue
        m = re.search(r"between\s+(-?[\d.]+)\s+(?:and|to|-)\s+(-?[\d.]+)", low)
        one_of = re.search(r"one of ([^.]+)", low)
        least = re.search(r"(?:at least|minimum|>=)\s*(-?[\d.]+)", low)
        most = re.search(r"(?:at most|maximum|<=|no more than)\s*(-?[\d.]+)", low)
        # intent words first, column-type words last ("email must be unique" is a uniqueness rule)
        if m:
            rules.append({"type": "range", "column": col, "min": float(m[1]), "max": float(m[2])})
        elif re.search(r"unique|no duplicate", low):
            rules.append({"type": "unique", "column": col})
        elif re.search(r"not (be )?(empty|blank|missing|null)|required|mandatory", low):
            rules.append({"type": "not_null", "column": col})
        elif one_of:
            vals = [v.strip(" '\"") for v in re.split(r"[/|]|\bor\b|,", one_of[1]) if v.strip()]
            rules.append({"type": "allowed", "column": col, "values": vals})
        elif least:
            rules.append({"type": "range", "column": col, "min": float(least[1])})
        elif most:
            rules.append({"type": "range", "column": col, "max": float(most[1])})
        elif re.search(r"mobile|phone", low):
            rules.append({"type": "bd_mobile", "column": col})
        elif "nid" in low:
            rules.append({"type": "bd_nid", "column": col})
        elif "email" in low:
            rules.append({"type": "email", "column": col})
        elif re.search(r"\bdate\b", low):
            rules.append({"type": "date", "column": col})
    return rules


# ---------------------------------------------------------------------------------- engine entry point
def run_validation(df: pd.DataFrame, profile: Dict, custom_rules: Optional[List[Dict]] = None,
                   llm: Optional[LLM] = None, use_ai: bool = False, lang: str = "en",
                   use_inferred: bool = True) -> Dict:
    rules = infer_rules(df, profile) if use_inferred else []
    rules = rules + sanitize_rules(custom_rules or [], df.columns)
    issues: List[Dict] = []
    for r in rules:
        fn = CHECKS.get(r["type"])
        if fn:
            try:
                issues.extend(fn(df, r))
            except KeyError:
                continue
    if use_ai and llm:
        issues.extend(ai_checks(df, profile, llm, lang))
    dup_rows = {i["row"] for i in issues if i["rule"] == "duplicate_rows"}
    issues = [i for i in issues if not (i["rule"] == "unique" and i["row"] in dup_rows)]
    # de-duplicate identical (row, column, rule)
    seen, uniq = set(), []
    for it in issues:
        k = (it["row"], it["column"], it["rule"], it["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    uniq.sort(key=lambda x: (x["row"], x["column"]))
    return {"issues": uniq, "rules_applied": len(rules), "summary": summarize(df, uniq),
            "score": quality_score(df, uniq)}


def summarize(df, issues) -> Dict:
    by_rule, by_col, by_sev = {}, {}, {"error": 0, "warning": 0}
    for it in issues:
        by_rule[it["rule"]] = by_rule.get(it["rule"], 0) + 1
        by_col[it["column"]] = by_col.get(it["column"], 0) + 1
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
    return {"total": len(issues), "by_rule": by_rule, "by_column": by_col, "by_severity": by_sev,
            "fixable": sum(1 for i in issues if i["fixable"]),
            "rows_affected": len({i["row"] for i in issues})}


def quality_score(df, issues) -> Dict:
    """0-100 score from four dimensions (row-based so one bad record in 50 is clearly visible)."""
    n = max(len(df), 1)

    def rows(pred):
        return len({i["row"] for i in issues if pred(i)})
    comp = 1 - rows(lambda i: i["rule"] in COMPLETENESS_RULES) / n
    uniq = 1 - rows(lambda i: i["rule"] in UNIQUENESS_RULES) / n
    cons = 1 - rows(lambda i: i["rule"] in CONSISTENCY_RULES or i["rule"] == "trim") / n
    valid = 1 - rows(lambda i: i["rule"] not in COMPLETENESS_RULES | UNIQUENESS_RULES | CONSISTENCY_RULES
                     and i["rule"] != "trim") / n
    overall = 100 * (0.30 * comp + 0.40 * valid + 0.20 * uniq + 0.10 * cons)
    grade = "A" if overall >= 95 else "B" if overall >= 85 else "C" if overall >= 70 else "D" if overall >= 50 else "F"
    return {"overall": round(overall, 1), "grade": grade,
            "completeness": round(100 * comp, 1), "validity": round(100 * valid, 1),
            "uniqueness": round(100 * uniq, 1), "consistency": round(100 * cons, 1)}
