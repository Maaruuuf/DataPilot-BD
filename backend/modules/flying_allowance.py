"""Module 12: Monthly flying-allowance sheet for riding engineers (computation + Excel export)."""
import io
from typing import Dict, List, Optional
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from ..core.dataio import is_null
from ..core.localization import to_number


def _find(cols, *hints):
    for h in hints:
        for c in cols:
            if h in c.lower():
                return c
    return None


def compute(df: pd.DataFrame, hourly_rate: float, sector_rate: float, cap_hours: float, currency: str = "BDT",
            month_label: str = "", hours_col: Optional[str] = None, sectors_col: Optional[str] = None) -> Dict:
    cols = list(df.columns)
    hours_col = hours_col or _find(cols, "flying_hours", "flight_hours", "hours")
    sectors_col = sectors_col or _find(cols, "sectors", "sector")
    flag_col = _find(cols, "riding_engineer", "riding")
    name_c = _find(cols, "full_name", "name")
    desig_c = _find(cols, "designation")
    stat_c = _find(cols, "status")
    if not hours_col:
        raise ValueError("No flying-hours column found. Pick one in the mapping.")
    payable, exceptions = [], []
    for i in range(len(df)):
        rec = df.iloc[i]
        hrs = to_number(rec[hours_col]) or 0.0
        sec = int(to_number(rec[sectors_col]) or 0) if sectors_col else 0
        is_rider = str(rec[flag_col]).strip().upper() == "Y" if flag_col else hrs > 0
        status = str(rec[stat_c]).strip().lower() if stat_c else "active"
        base = {"emp_id": rec[cols[0]], "name": rec[name_c] if name_c else "", "designation": rec[desig_c] if desig_c else "",
                "flying_hours": hrs, "sectors": sec}
        if hrs <= 0 and sec <= 0:
            continue
        if not is_rider:
            exceptions.append({**base, "issue": "Has flying hours but is not marked as a riding engineer - verify before paying."})
            continue
        if status in ("resigned", "separated", "terminated"):
            exceptions.append({**base, "issue": f"Status is '{status}' - confirm eligibility."})
            continue
        remarks = ""
        pay_hours = hrs
        if hrs > cap_hours:
            pay_hours = cap_hours
            remarks = f"Hours {hrs:g} exceed cap {cap_hours:g}; paid at cap pending management approval."
            exceptions.append({**base, "issue": remarks})
        payable.append({**base, "payable_hours": pay_hours, "remarks": remarks,
                        "amount": round(pay_hours * hourly_rate + sec * sector_rate, 2)})
    total = round(sum(p["amount"] for p in payable), 2)
    return {"month": month_label, "currency": currency, "hourly_rate": hourly_rate, "sector_rate": sector_rate,
            "cap_hours": cap_hours, "rows": payable, "exceptions": exceptions, "total": total,
            "headcount": len(payable), "total_hours": round(sum(p["flying_hours"] for p in payable), 1)}


def to_excel(res: Dict, prepared_by: str = "") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Flying Allowance"
    f = "Arial"
    bold, hdr_fill = Font(name=f, bold=True), PatternFill("solid", fgColor="1E3A8A")
    thin = Side(style="thin", color="94A3B8")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["A1"] = f"Flying Allowance Sheet - Riding Engineers - {res['month']}"
    ws["A1"].font = Font(name=f, bold=True, size=14)
    ws["A3"], ws["B3"] = "Rate per flying hour", res["hourly_rate"]
    ws["A4"], ws["B4"] = "Rate per sector", res["sector_rate"]
    ws["A5"], ws["B5"] = "Monthly cap (hours)", res["cap_hours"]
    ws["D3"], ws["E3"] = "Currency", res["currency"]
    ws["A6"] = "Rates are inputs (blue). Edit them and every amount recalculates."
    ws["A6"].font = Font(name=f, italic=True, size=9, color="64748B")
    for r in (3, 4, 5):
        ws[f"A{r}"].font = bold
        ws[f"B{r}"].font = Font(name=f, color="0000FF", bold=True)
    ws["D3"].font = bold
    heads = ["SL", "Emp ID", "Name", "Designation", "Flying Hours", "Sectors", "Payable Hours",
             "Hourly Amount", "Sector Amount", "Total Allowance", "Remarks"]
    hr = 8
    for j, h in enumerate(heads, 1):
        c = ws.cell(hr, j, h)
        c.font, c.fill, c.border = Font(name=f, bold=True, color="FFFFFF"), hdr_fill, box
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    r = hr
    for n, p in enumerate(res["rows"], 1):
        r += 1
        vals = [n, p["emp_id"], p["name"], p["designation"], p["flying_hours"], p["sectors"],
                f"=MIN(E{r},$B$5)", f"=G{r}*$B$3", f"=F{r}*$B$4", f"=H{r}+I{r}", p["remarks"]]
        for j, v in enumerate(vals, 1):
            c = ws.cell(r, j, v)
            c.font, c.border = Font(name=f), box
            if j in (8, 9, 10):
                c.number_format = '#,##0.00'
    first, last = hr + 1, r
    r += 1
    ws.cell(r, 1, "TOTAL").font = bold
    for j, col in ((5, "E"), (6, "F"), (7, "G"), (8, "H"), (9, "I"), (10, "J")):
        c = ws.cell(r, j, f"=SUM({col}{first}:{col}{last})" if last >= first else 0)
        c.font, c.border = bold, box
        if j >= 8:
            c.number_format = '#,##0.00'
    r += 3
    for j, label in ((2, "Prepared by"), (5, "Checked by"), (9, "Approved by")):
        ws.cell(r, j, "________________").font = Font(name=f)
        ws.cell(r + 1, j, label + (f": {prepared_by}" if label == "Prepared by" and prepared_by else "")).font = bold
    for j, w in enumerate([5, 14, 26, 28, 13, 10, 14, 15, 15, 17, 48], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(hr + 1, 1)

    ex = wb.create_sheet("Exceptions")
    for j, h in enumerate(["Emp ID", "Name", "Flying Hours", "Sectors", "Issue"], 1):
        c = ex.cell(1, j, h)
        c.font, c.fill = Font(name=f, bold=True, color="FFFFFF"), PatternFill("solid", fgColor="B91C1C")
    for i, e in enumerate(res["exceptions"], 2):
        for j, v in enumerate([e["emp_id"], e["name"], e["flying_hours"], e["sectors"], e["issue"]], 1):
            ex.cell(i, j, v).font = Font(name=f)
    for j, w in enumerate([14, 26, 14, 10, 90], 1):
        ex.column_dimensions[get_column_letter(j)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
