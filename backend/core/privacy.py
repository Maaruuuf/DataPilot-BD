"""Privacy mode: mask PII before anything is sent to an external LLM.

Two mechanisms:
  * mask_df / mask_value  - partial masking of PII *columns* (used for samples, chat tool results)
  * Tokenizer            - reversible placeholders for PII found inside *free text* (used by Smart Entry,
                           letters): the LLM sees <PHONE_1>, we map it back in the answer.
"""
import re
from typing import Any
import pandas as pd

PII_NAME_HINTS = ("mobile", "phone", "contact", "email", "nid", "passport", "bank", "account", "tin",
                  "d_pass", "dpass", "address")

PHONE_RE = re.compile(r"(?:\+?88)?[0০]?[1১][3-9৩-৯][\d০-৯]{8}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LONG_ID_RE = re.compile(r"\b[\d০-৯]{10,17}\b")


def pii_columns(columns) -> list:
    out = []
    for c in columns:
        low = str(c).lower()
        if any(h in low for h in PII_NAME_HINTS):
            out.append(c)
    return out


def mask_value(v: Any) -> str:
    s = str(v)
    if not s.strip():
        return s
    if "@" in s:
        user, _, dom = s.partition("@")
        return (user[:1] + "***@" + dom) if user else "***@" + dom
    if len(s) <= 4:
        return "*" * len(s)
    return s[:3] + "*" * max(len(s) - 7, 2) + s[-4:]


def mask_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in pii_columns(out.columns):
        out[c] = out[c].map(lambda v: mask_value(v) if str(v).strip() else v)
    return out


class Tokenizer:
    """Replace phone/email/long-ID strings by placeholders, remember the mapping."""

    def __init__(self):
        self.map = {}
        self._n = {"PHONE": 0, "EMAIL": 0, "ID": 0}

    def _sub(self, kind):
        def f(m):
            val = m.group(0)
            for k, v in self.map.items():
                if v == val:
                    return k
            self._n[kind] += 1
            tok = f"<{kind}_{self._n[kind]}>"
            self.map[tok] = val
            return tok
        return f

    def mask(self, text: str) -> str:
        text = EMAIL_RE.sub(self._sub("EMAIL"), text)
        text = PHONE_RE.sub(self._sub("PHONE"), text)
        text = LONG_ID_RE.sub(self._sub("ID"), text)
        return text

    def unmask(self, obj):
        if isinstance(obj, str):
            for tok, val in self.map.items():
                obj = obj.replace(tok, val)
            return obj
        if isinstance(obj, list):
            return [self.unmask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v) for k, v in obj.items()}
        return obj
