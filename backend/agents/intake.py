"""Intake Agent: schema/type detection + data profiling (Features 1 & 2)."""
import re
from typing import Dict, List
import pandas as pd

from ..core.dataio import is_null
from ..core.localization import to_number, parse_date_flex

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
BOOL_VALUES = {"y", "n", "yes", "no", "true", "false"}


def tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def _share(values, fn) -> float:
    vals = [v for v in values if not is_null(v)]
    if not vals:
        return 0.0
    return sum(1 for v in vals if fn(v)) / len(vals)


def infer_kind(name: str, series: pd.Series) -> str:
    """email | phone | nid | date | integer | float | boolean | id | category | text"""
    toks = tokens(name)
    low = name.lower()
    vals = [v for v in series.tolist() if not is_null(v)]
    n_unique = len(set(vals))
    ratio = n_unique / max(len(vals), 1)

    if "email" in toks or "mail" in toks or _share(vals, lambda v: bool(EMAIL_RE.match(v))) > 0.6:
        return "email"
    if any(t in toks for t in ("mobile", "phone", "cell", "contact")) and "date" not in toks:
        return "phone"
    if "nid" in toks or "national" in toks:
        return "nid"
    if any(t in toks for t in ("date", "dob", "expiry", "expire", "due")) or low.endswith(("_on", "_at")):
        if _share(vals, lambda v: parse_date_flex(v)[0] is not None) >= 0.6:
            return "date"
    if _share(vals, lambda v: parse_date_flex(v)[0] is not None) >= 0.85 and vals:
        return "date"
    if "id" in toks or low.endswith("_id") or (toks and toks[-1] in ("no", "number", "code") and ratio >= 0.9):
        return "id"
    if vals and all(v.strip().lower() in BOOL_VALUES for v in vals) and n_unique <= 3:
        return "boolean"
    num_share = _share(vals, lambda v: to_number(v) is not None)
    if num_share >= 0.9 and vals:
        nums = [to_number(v) for v in vals if to_number(v) is not None]
        return "integer" if all(float(x).is_integer() for x in nums) else "float"
    if vals and (n_unique <= 10 or (n_unique <= 25 and ratio <= 0.5)):
        return "category"
    return "text"


def profile_dataframe(df: pd.DataFrame) -> Dict:
    cols = []
    total_nulls = 0
    for c in df.columns:
        s = df[c]
        kind = infer_kind(c, s)
        nulls = int(sum(is_null(v) for v in s))
        total_nulls += nulls
        vals = [v for v in s.tolist() if not is_null(v)]
        info = {
            "name": c, "kind": kind, "null_count": nulls,
            "null_pct": round(100 * nulls / max(len(df), 1), 1),
            "unique": len(set(vals)),
            "sample": vals[:3],
            "top_values": [{"value": k, "count": int(v)} for k, v in
                           pd.Series(vals).value_counts().head(5).items()] if vals else [],
        }
        if kind in ("integer", "float"):
            nums = pd.Series([to_number(v) for v in vals]).dropna()
            if len(nums):
                info.update(min=float(nums.min()), max=float(nums.max()),
                            mean=round(float(nums.mean()), 2), median=float(nums.median()))
        if kind == "date":
            ds = [parse_date_flex(v)[0] for v in vals]
            ds = [d for d in ds if d]
            if ds:
                info.update(min=min(ds).isoformat(), max=max(ds).isoformat())
        cols.append(info)
    id_like = [c["name"] for c in cols if c["kind"] == "id"]
    key_cols = [c for c in df.columns if c not in id_like]
    dup_rows = int(df.duplicated(subset=key_cols, keep="first").sum()) if key_cols else 0
    return {
        "rows": int(len(df)), "columns": int(df.shape[1]),
        "missing_cells": total_nulls,
        "missing_pct": round(100 * total_nulls / max(df.size, 1), 2),
        "duplicate_rows": dup_rows,
        "column_info": cols,
    }


def typed_frame(df: pd.DataFrame, profile: Dict) -> pd.DataFrame:
    """Typed copy used for analytics (chat, reports): numbers -> float, dates -> datetime."""
    out = df.copy()
    for c in profile["column_info"]:
        name, kind = c["name"], c["kind"]
        if kind in ("integer", "float"):
            out[name] = out[name].map(to_number)
        elif kind == "date":
            out[name] = pd.to_datetime(out[name].map(
                lambda v: parse_date_flex(v)[0] if not is_null(v) else None), errors="coerce")
        else:
            out[name] = out[name].map(lambda v: None if is_null(v) else v)
    return out
