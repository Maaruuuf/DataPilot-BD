"""Module 14: partner-airline invoices, payment tracking, aging and reminders."""
import html
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
from sqlalchemy.orm import Session

from ..database import Invoice
from ..core.dataio import is_null
from ..core.localization import fmt_bdt, to_number, parse_date_flex

SERVICE_TYPES = ["Line maintenance / transit check", "AOG support", "Component repair / exchange", "Spare part loan",
                 "Engineer deputation (man-hours)", "Borescope / NDT inspection", "Training seat",
                 "Technical records audit", "Base maintenance support", "Other"]
VALID_STATUS = ("pending", "submitted_to_accounts", "paid")


def next_number(db: Session, year: Optional[int] = None) -> str:
    y = year or date.today().year
    n = db.query(Invoice).filter(Invoice.number.like(f"INV-{y}-%")).count() + 1
    num = f"INV-{y}-{n:04d}"
    while db.query(Invoice).filter(Invoice.number == num).first():
        n += 1
        num = f"INV-{y}-{n:04d}"
    return num


def compute_totals(items: List[Dict], vat_pct: float, currency: str, rate: float) -> Dict:
    clean = []
    for it in items:
        q, p = float(it.get("quantity") or 0), float(it.get("unit_price") or 0)
        clean.append({"description": it.get("description", ""), "quantity": q, "unit_price": p, "amount": round(q * p, 2)})
    sub = round(sum(i["amount"] for i in clean), 2)
    total = round(sub * (1 + vat_pct / 100), 2)
    bdt = total if currency == "BDT" else round(total * (rate or 1), 2)
    return {"items": clean, "subtotal": sub, "total": total, "total_bdt": bdt}


def effective_status(inv: Invoice, today: Optional[date] = None) -> str:
    today = today or date.today()
    if inv.status == "paid":
        return "paid"
    d = parse_date_flex(inv.due_date)[0] if inv.due_date else None
    if d and d < today:
        return "overdue"
    return inv.status


def days_overdue(inv: Invoice, today: Optional[date] = None) -> int:
    today = today or date.today()
    d = parse_date_flex(inv.due_date)[0] if inv.due_date else None
    return max((today - d).days, 0) if d and inv.status != "paid" else 0


def to_dict(inv: Invoice) -> Dict:
    return {"id": inv.id, "number": inv.number, "partner": inv.partner, "country": inv.country, "direction": inv.direction,
            "service_type": inv.service_type, "currency": inv.currency, "exchange_rate": inv.exchange_rate,
            "items": inv.items, "subtotal": inv.subtotal, "vat_pct": inv.vat_pct, "total": inv.total,
            "total_bdt": inv.total_bdt, "issue_date": inv.issue_date, "due_date": inv.due_date, "status": inv.status,
            "effective_status": effective_status(inv), "days_overdue": days_overdue(inv),
            "submitted_date": inv.submitted_date, "paid_date": inv.paid_date, "notes": inv.notes}


def create_invoice(db: Session, data: Dict) -> Invoice:
    cur = (data.get("currency") or "USD").upper()
    rate = float(data.get("exchange_rate") or (1 if cur == "BDT" else 110))
    t = compute_totals(data.get("items") or [], float(data.get("vat_pct") or 0), cur, rate)
    issue = data.get("issue_date") or date.today().isoformat()
    due = data.get("due_date") or (parse_date_flex(issue)[0] + timedelta(days=int(data.get("terms_days") or 30))).isoformat()
    inv = Invoice(number=data.get("number") or next_number(db), partner=data["partner"], country=data.get("country"),
                  direction=data.get("direction", "provided"), service_type=data.get("service_type"), currency=cur,
                  exchange_rate=rate, items=t["items"], subtotal=t["subtotal"], vat_pct=float(data.get("vat_pct") or 0),
                  total=t["total"], total_bdt=t["total_bdt"], issue_date=issue, due_date=due,
                  status=data.get("status", "pending"), submitted_date=data.get("submitted_date"),
                  paid_date=data.get("paid_date"), notes=data.get("notes"))
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def import_dataframe(db: Session, df: pd.DataFrame) -> Dict:
    """Bulk import from CSV. Columns used: invoice_no, partner_airline, country, direction, service_type, description,
    currency, amount, exchange_rate, issue_date, due_date, status, submitted_to_accounts_date, paid_date"""
    created, skipped = 0, []
    g = lambda r, k: (None if k not in r.index or is_null(r[k]) else str(r[k]).strip())
    for i in range(len(df)):
        r = df.iloc[i]
        num, partner, amt = g(r, "invoice_no"), g(r, "partner_airline") or g(r, "partner"), to_number(g(r, "amount"))
        if not partner or amt is None:
            skipped.append({"row": i + 2, "reason": "missing partner or amount"})
            continue
        if num and db.query(Invoice).filter(Invoice.number == num).first():
            skipped.append({"row": i + 2, "reason": f"{num} already exists"})
            continue
        status = (g(r, "status") or "pending").lower().replace(" ", "_")
        status = status if status in VALID_STATUS else "pending"
        create_invoice(db, {"number": num, "partner": partner, "country": g(r, "country"),
                            "direction": (g(r, "direction") or "provided").lower(), "service_type": g(r, "service_type"),
                            "currency": g(r, "currency") or "USD", "exchange_rate": to_number(g(r, "exchange_rate")),
                            "items": [{"description": g(r, "description") or g(r, "service_type") or "Technical service",
                                       "quantity": 1, "unit_price": amt}],
                            "issue_date": _iso(g(r, "issue_date")), "due_date": _iso(g(r, "due_date")),
                            "status": status, "submitted_date": _iso(g(r, "submitted_to_accounts_date")),
                            "paid_date": _iso(g(r, "paid_date"))})
        created += 1
    return {"created": created, "skipped": skipped}


def _iso(v):
    if not v:
        return None
    d = parse_date_flex(v)[0]
    return d.isoformat() if d else None


def summary(db: Session, today: Optional[date] = None) -> Dict:
    today = today or date.today()
    invs = db.query(Invoice).all()
    by_status, aging, by_partner = {}, {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}, {}
    for inv in invs:
        st = effective_status(inv, today)
        s = by_status.setdefault(st, {"count": 0, "total_bdt": 0.0})
        s["count"] += 1
        s["total_bdt"] = round(s["total_bdt"] + inv.total_bdt, 2)
        if inv.direction == "provided" and inv.status != "paid":
            p = by_partner.setdefault(inv.partner, {"outstanding_bdt": 0.0, "invoices": 0})
            p["outstanding_bdt"] = round(p["outstanding_bdt"] + inv.total_bdt, 2)
            p["invoices"] += 1
            d = days_overdue(inv, today)
            k = "current" if d == 0 else "1-30" if d <= 30 else "31-60" if d <= 60 else "61-90" if d <= 90 else "90+"
            aging[k] = round(aging[k] + inv.total_bdt, 2)
    out = round(sum(v["outstanding_bdt"] for v in by_partner.values()), 2)
    return {"count": len(invs), "by_status": by_status, "aging_bdt": aging, "outstanding_bdt": out,
            "outstanding_fmt": fmt_bdt(out), "by_partner": dict(sorted(by_partner.items(), key=lambda x: -x[1]["outstanding_bdt"]))}


def invoice_html(inv: Invoice, org: str = "Airline (Demo) - Engineering Department") -> str:
    rows = "".join(
        f"<tr><td>{html.escape(str(i['description']))}</td><td class=r>{i['quantity']:g}</td>"
        f"<td class=r>{i['unit_price']:,.2f}</td><td class=r>{i['amount']:,.2f}</td></tr>" for i in (inv.items or []))
    vat = round(inv.total - inv.subtotal, 2)
    bdt_line = "" if inv.currency == "BDT" else (
        f"<tr><td colspan=3 class=r>Equivalent in BDT (1 {inv.currency} = {inv.exchange_rate:g} BDT)</td>"
        f"<td class=r>{fmt_bdt(inv.total_bdt)}</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{inv.number}</title><style>
body{{font-family:Arial,sans-serif;max-width:780px;margin:30px auto;color:#0f172a}} table{{width:100%;border-collapse:collapse;margin-top:16px}}
td,th{{border:1px solid #cbd5e1;padding:7px 10px;font-size:14px}} th{{background:#1e3a8a;color:#fff;text-align:left}} .r{{text-align:right}}
h1{{margin:0}} .m{{color:#475569;font-size:13px}} @media print{{button{{display:none}}}}</style></head><body>
<h1>INVOICE</h1><div class=m>{html.escape(org)}</div><p><b>No:</b> {inv.number} &nbsp; <b>Issued:</b> {inv.issue_date} &nbsp; <b>Due:</b> {inv.due_date}</p>
<p><b>Bill to:</b> {html.escape(inv.partner or '')} {('(' + html.escape(inv.country) + ')') if inv.country else ''}<br><b>Service:</b> {html.escape(inv.service_type or '')}</p>
<table><tr><th>Description</th><th class=r>Qty</th><th class=r>Unit price ({inv.currency})</th><th class=r>Amount ({inv.currency})</th></tr>{rows}
<tr><td colspan=3 class=r>Subtotal</td><td class=r>{inv.subtotal:,.2f}</td></tr>
<tr><td colspan=3 class=r>VAT/Tax ({inv.vat_pct:g}%)</td><td class=r>{vat:,.2f}</td></tr>
<tr><td colspan=3 class=r><b>Total ({inv.currency})</b></td><td class=r><b>{inv.total:,.2f}</b></td></tr>{bdt_line}</table>
<p class=m>{html.escape(inv.notes or '')}</p><button onclick="window.print()">Print / Save as PDF</button></body></html>"""
