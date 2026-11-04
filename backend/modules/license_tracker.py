"""Module 11: License / certificate / authorization / training / pass expiry tracker (QA coordination)."""
from datetime import date
from typing import Dict, List, Optional
import pandas as pd

from ..core.dataio import is_null
from ..core.localization import parse_date_flex, today as _today

# (label, column-name keywords that identify the expiry column)
ITEM_KEYS = [
    ("License", ("license", "expiry")),
    ("Authorization", ("authorization", "expiry")),
    ("CAAB Pass", ("caab", "expiry")),
    ("D-Pass", ("dpass", "expiry")),
    ("Work Permit", ("work", "permit")),
    ("Visa", ("visa",)),
    ("Recurrent Training", ("training", "due")),
    ("Certificate", ("certificate", "expiry")),
]

ACTIONS = {
    "expired": "STOP certifying until validated. Send extension/renewal request to QA immediately.",
    "critical": "Initiate renewal now (≤30 days). Raise letter to QA / Security / Admin & Protocol.",
    "warning": "Start renewal paperwork (31-60 days). Book training seat if applicable.",
    "upcoming": "Plan renewal (61-90 days). Add to next coordination meeting.",
    "valid": "No action.",
    "missing": "Expiry date not recorded - update the staff record.",
}
OWNER = {"License": "QA Department", "Authorization": "QA Department", "Certificate": "QA Department",
         "Recurrent Training": "QA / Training", "CAAB Pass": "Security Department",
         "D-Pass": "Security Department", "Work Permit": "Admin & Protocol", "Visa": "Admin & Protocol"}


def find_columns(columns: List[str]) -> Dict[str, str]:
    found = {}
    for label, keys in ITEM_KEYS:
        for c in columns:
            low = c.lower().replace("-", "").replace(" ", "_")
            if all(k in low for k in keys):
                found[label] = c
                break
    return found


def _col(columns, *hints):
    for h in hints:
        for c in columns:
            if h in c.lower():
                return c
    return None


def status_for(days: Optional[int], critical=30, warning=60, upcoming=90) -> str:
    if days is None:
        return "missing"
    if days < 0:
        return "expired"
    if days <= critical:
        return "critical"
    if days <= warning:
        return "warning"
    if days <= upcoming:
        return "upcoming"
    return "valid"


def track(df: pd.DataFrame, reference_date: Optional[date] = None, mapping: Optional[Dict[str, str]] = None,
          include_inactive: bool = False) -> Dict:
    ref = reference_date or _today()
    cols = list(df.columns)
    items = mapping or find_columns(cols)
    id_c = cols[0]
    name_c = _col(cols, "full_name", "name")
    desig_c = _col(cols, "designation", "position", "title")
    nat_c = _col(cols, "nationality", "citizen")
    stat_c = _col(cols, "status")
    rows, expat_gaps = [], []
    for i in range(len(df)):
        rec = df.iloc[i]
        status_val = str(rec[stat_c]).strip().lower() if stat_c else "active"
        if not include_inactive and status_val in ("resigned", "separated", "terminated", "retired"):
            continue
        for label, col in items.items():
            raw = rec[col]
            d = parse_date_flex(raw)[0] if not is_null(raw) else None
            days = (d - ref).days if d else None
            st = status_for(days)
            rows.append({"emp_id": rec[id_c], "name": rec[name_c] if name_c else "", "designation": rec[desig_c] if desig_c else "",
                         "item": label, "column": col, "expiry_date": d.isoformat() if d else "",
                         "days_left": days, "status": st, "coordinate_with": OWNER.get(label, "QA Department"),
                         "action": ACTIONS[st], "row": i})
        if nat_c and not is_null(rec[nat_c]) and str(rec[nat_c]).strip().lower() not in ("bangladeshi", "bangladesh", "bd"):
            for need in ("Work Permit", "Visa"):
                if need not in items:
                    expat_gaps.append({"emp_id": rec[id_c], "name": rec[name_c] if name_c else "",
                                       "nationality": rec[nat_c], "missing": need,
                                       "coordinate_with": OWNER[need],
                                       "action": f"Expatriate AME: no '{need}' expiry column in the data. Add it so renewals via Admin & Protocol are tracked."})
    order = {"expired": 0, "critical": 1, "warning": 2, "upcoming": 3, "missing": 4, "valid": 5}
    rows.sort(key=lambda r: (order[r["status"]], r["days_left"] if r["days_left"] is not None else 99999))
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in order}
    people = {r["emp_id"] for r in rows if r["status"] in ("expired", "critical")}
    return {"reference_date": ref.isoformat(), "items_found": items, "counts": counts, "total_checks": len(rows),
            "staff_needing_urgent_action": len(people), "rows": rows, "expat_gaps": expat_gaps}
