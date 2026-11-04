"""Chat Agent: talk to the uploaded CSV (Feature 7).

The LLM never sees the whole file and never writes code. It calls ONE tool, `query_data`, which takes a
structured spec and is executed by pandas here. Every number in an answer therefore comes from a real
computation, which is what prevents hallucinated figures.
"""
import json
from typing import Dict, List
import pandas as pd

from ..core.llm import LLM, LANG_NAME
from ..core.privacy import mask_df
from ..core.localization import fmt_bdt
from .intake import typed_frame

MAX_ROWS = 50
AGGS = ["sum", "mean", "median", "min", "max", "count", "nunique"]
OPS = ["==", "!=", ">", ">=", "<", "<=", "contains", "in", "isnull", "notnull"]

TOOLS = [{
    "type": "function",
    "function": {
        "name": "query_data",
        "description": ("Run a read-only query on the uploaded dataset and get a table back. Operations: "
                        "'aggregate' (optionally group_by), 'filter' (list rows), 'value_counts' (frequency of a "
                        "column), 'describe' (stats of a numeric column), 'count' (number of rows after filters)."),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["aggregate", "filter", "value_counts", "describe", "count"]},
                "column": {"type": "string", "description": "Column to aggregate/describe/count values of"},
                "agg": {"type": "string", "enum": AGGS},
                "group_by": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "array", "items": {"type": "object", "properties": {
                    "column": {"type": "string"}, "op": {"type": "string", "enum": OPS},
                    "value": {"type": ["string", "number", "array", "null"]}},
                    "required": ["column", "op"]}},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to show for 'filter'"},
                "sort_by": {"type": "string"}, "ascending": {"type": "boolean"},
                "limit": {"type": "integer", "description": "max rows (<=50)"},
                "chart": {"type": "string", "enum": ["none", "bar", "line", "pie"],
                          "description": "Chart to draw from the result (first column = x, last numeric column = y)"},
            },
            "required": ["operation"],
        },
    },
}]


def _apply_filters(tdf: pd.DataFrame, filters) -> pd.DataFrame:
    for f in filters or []:
        col, op, val = f.get("column"), f.get("op"), f.get("value")
        if col not in tdf.columns:
            raise ValueError(f"Unknown column '{col}'")
        s = tdf[col]
        if op == "isnull":
            tdf = tdf[s.isna()]
        elif op == "notnull":
            tdf = tdf[s.notna()]
        elif op == "contains":
            tdf = tdf[s.astype(str).str.contains(str(val), case=False, na=False, regex=False)]
        elif op == "in":
            vals = val if isinstance(val, list) else [val]
            tdf = tdf[s.astype(str).str.lower().isin([str(v).lower() for v in vals])]
        else:
            if pd.api.types.is_numeric_dtype(s):
                val = float(val)
            elif pd.api.types.is_datetime64_any_dtype(s):
                val = pd.to_datetime(val)
            else:
                val = str(val)
                if op in ("==", "!="):
                    s = s.astype(str).str.lower()
                    val = val.lower()
            tdf = tdf[{"==": s == val, "!=": s != val, ">": s > val, ">=": s >= val, "<": s < val, "<=": s <= val}[op]]
    return tdf


def execute_query(tdf: pd.DataFrame, spec: Dict) -> pd.DataFrame:
    """Pure, side-effect-free executor for a query spec. Raises ValueError on bad specs."""
    op = spec.get("operation")
    limit = min(int(spec.get("limit") or 20), MAX_ROWS)
    col, agg = spec.get("column"), spec.get("agg") or "count"
    work = _apply_filters(tdf, spec.get("filters"))
    for c in (spec.get("group_by") or []) + ([col] if col else []) + (spec.get("columns") or []):
        if c not in work.columns:
            raise ValueError(f"Unknown column '{c}'. Available: {list(work.columns)}")
    if op == "count":
        return pd.DataFrame({"rows": [len(work)]})
    if op == "value_counts":
        if not col:
            raise ValueError("'column' is required")
        res = work[col].value_counts(dropna=True).reset_index()
        res.columns = [col, "count"]
        return res.head(limit)
    if op == "describe":
        if not col:
            raise ValueError("'column' is required")
        d = work[col].describe()
        return pd.DataFrame({"statistic": d.index.astype(str), col: d.values})
    if op == "filter":
        res = work[spec["columns"]] if spec.get("columns") else work
        if spec.get("sort_by") in res.columns:
            res = res.sort_values(spec["sort_by"], ascending=bool(spec.get("ascending", False)))
        return res.head(limit)
    if op == "aggregate":
        if agg not in AGGS:
            raise ValueError(f"Unknown agg '{agg}'")
        gb = spec.get("group_by") or []
        if agg in ("sum", "mean", "median") and col and not pd.api.types.is_numeric_dtype(work[col]):
            raise ValueError(f"Column '{col}' is not numeric; use count or nunique")
        if gb:
            target = col or gb[0]
            res = work.groupby(gb, dropna=True)[target].agg(agg).reset_index()
            res.columns = gb + [f"{agg}_{target}"]
            by = spec.get("sort_by") if spec.get("sort_by") in res.columns else res.columns[-1]
            res = res.sort_values(by, ascending=bool(spec.get("ascending", False)))
            return res.head(limit)
        if not col:
            raise ValueError("'column' is required for aggregate")
        val = getattr(work[col], agg)() if agg != "nunique" else work[col].nunique()
        return pd.DataFrame({"metric": [f"{agg}_{col}"], "value": [val]})
    raise ValueError(f"Unknown operation '{op}'")


def _jsonable(df: pd.DataFrame) -> List[Dict]:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(out.notna(), None)
    recs = out.to_dict("records")
    for r in recs:
        for k, v in r.items():
            if hasattr(v, "item"):
                r[k] = v.item()
            if isinstance(r[k], float):
                r[k] = round(r[k], 2)
    return recs


def _chart_from(res: pd.DataFrame, kind: str) -> Dict:
    num = [c for c in res.columns[1:] if pd.api.types.is_numeric_dtype(res[c])]
    if kind in (None, "none") or len(res.columns) < 2 or not num:
        return {}
    return {"type": "chart", "kind": kind, "x": str(res.columns[0]), "y": str(num[-1]), "data": _jsonable(res)}


def system_prompt(profile: Dict, sample: pd.DataFrame, lang: str) -> str:
    cols = "\n".join(f"- {c['name']} [{c['kind']}] e.g. {c['sample'][:2]}" for c in profile["column_info"])
    return (
        "You are DataPilot, an analyst assistant for a Bangladeshi airline engineering department. You answer "
        "questions about ONE uploaded dataset.\n"
        f"Dataset: {profile['rows']} rows x {profile['columns']} columns.\nColumns:\n{cols}\n\n"
        "Rules:\n1. Use the query_data tool for ANY number, count, ranking or list. Never compute or recall figures yourself.\n"
        "2. If the tool returns an error, fix your call and retry; if a column does not exist, say so.\n"
        "3. If the question cannot be answered from this data, say that plainly.\n"
        "4. Keep answers short: one-sentence answer first, then key figures. Tables/charts are shown automatically.\n"
        "5. Monetary values are BDT unless the column says otherwise. Dates are ISO yyyy-mm-dd.\n"
        f"6. Reply in {LANG_NAME.get(lang, 'English')}."
    )


def chat(df: pd.DataFrame, profile: Dict, message: str, history: List[Dict], llm: LLM, lang: str = "en",
         privacy: bool = True) -> Dict:
    if not llm.available:
        return {"answer": ("The AI chat needs a Groq API key. Add GROQ_API_KEY to your .env file and restart the "
                           "backend. Meanwhile: this dataset has "
                           f"{profile['rows']} rows and {profile['columns']} columns "
                           f"({', '.join(c['name'] for c in profile['column_info'][:8])}...)."),
                "artifacts": [], "tool_calls": []}
    tdf = typed_frame(df, profile)
    messages = [{"role": "system", "content": system_prompt(profile, df.head(3), lang)}]
    for h in (history or [])[-10:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})
    artifacts, calls = [], []
    for _ in range(5):
        msg = llm.chat(messages, tools=TOOLS, temperature=0.1)
        if not getattr(msg, "tool_calls", None):
            return {"answer": (msg.content or "").strip(), "artifacts": artifacts, "tool_calls": calls}
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                spec = json.loads(tc.function.arguments or "{}")
                res = execute_query(tdf, spec)
                calls.append(spec)
                recs = _jsonable(res)
                artifacts.append({"type": "table", "title": spec.get("operation", ""), "data": recs})
                ch = _chart_from(res, spec.get("chart"))
                if ch:
                    artifacts.append(ch)
                # what the LLM sees: privacy-masked (only affects PII columns)
                seen = mask_df(res.astype(str)) if privacy else res
                payload = {"rows_returned": len(res), "data": _jsonable(seen)[:30]}
            except Exception as e:  # report error to the model so it can retry
                payload = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(payload, default=str)})
    return {"answer": "I could not complete that analysis. Please rephrase the question.",
            "artifacts": artifacts, "tool_calls": calls}
