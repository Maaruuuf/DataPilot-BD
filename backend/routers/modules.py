"""Engineering Admin modules: license tracker, flying allowance, letters & minutes, invoices."""
import io
import time
from datetime import date
from typing import Dict, List, Optional
import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import MANUAL_MINUTES
from ..database import get_db, Invoice, GeneratedDocument, ActionItem
from ..core.dataio import read_table
from ..core.llm import get_llm
from ..modules import license_tracker, flying_allowance, letters, invoices
from ..agents import orchestrator as orch
from .datasets import actor_of, get_ds

router = APIRouter()


# ------------------------------------------------------------------------------------ 11. license tracker
class LicenseIn(BaseModel):
    reference_date: Optional[str] = None
    mapping: Optional[Dict[str, str]] = None
    include_inactive: bool = False


@router.post("/datasets/{ds_id}/licenses")
def licenses(ds_id: int, body: LicenseIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    t0 = time.time()
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds)
    ref = date.fromisoformat(body.reference_date) if body.reference_date else None
    res = license_tracker.track(df, ref, body.mapping, body.include_inactive)
    orch.audit(db, actor, "LicenseTracker", "license_check", ds_id,
               {"checks": res["total_checks"], "urgent_staff": res["staff_needing_urgent_action"]},
               rows=len(df), seconds=time.time() - t0, manual_minutes=MANUAL_MINUTES["license_check"])
    return res


# ------------------------------------------------------------------------------------ 12. flying allowance
class AllowanceIn(BaseModel):
    month: str = ""
    hourly_rate: float = 1500.0
    sector_rate: float = 500.0
    cap_hours: float = 100.0
    currency: str = "BDT"
    hours_column: Optional[str] = None
    sectors_column: Optional[str] = None
    prepared_by: str = ""


@router.post("/datasets/{ds_id}/flying-allowance")
def allowance(ds_id: int, body: AllowanceIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    t0 = time.time()
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds)
    try:
        res = flying_allowance.compute(df, body.hourly_rate, body.sector_rate, body.cap_hours, body.currency,
                                       body.month, body.hours_column, body.sectors_column)
    except ValueError as e:
        raise HTTPException(400, str(e))
    orch.audit(db, actor, "AllowanceModule", "flying_allowance", ds_id,
               {"headcount": res["headcount"], "total": res["total"], "exceptions": len(res["exceptions"])},
               rows=len(df), seconds=time.time() - t0, manual_minutes=MANUAL_MINUTES["allowance_sheet"])
    return res


@router.post("/datasets/{ds_id}/flying-allowance/excel")
def allowance_excel(ds_id: int, body: AllowanceIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds)
    try:
        res = flying_allowance.compute(df, body.hourly_rate, body.sector_rate, body.cap_hours, body.currency,
                                       body.month, body.hours_column, body.sectors_column)
    except ValueError as e:
        raise HTTPException(400, str(e))
    data = flying_allowance.to_excel(res, body.prepared_by or actor)
    orch.audit(db, actor, "AllowanceModule", "export_allowance_excel", ds_id, {"total": res["total"]})
    return StreamingResponse(io.BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="flying_allowance_{body.month or "month"}.xlsx"'})


# ------------------------------------------------------------------------------------ 13. letters & minutes
@router.get("/letters/types")
def letter_types():
    return {k: {"label": v["label"], "fields": v["fields"]} for k, v in letters.LETTER_TYPES.items()}


class LetterIn(BaseModel):
    letter_type: str
    fields: Dict = Field(default_factory=dict)
    lang: str = "en"
    use_ai: bool = True
    privacy: bool = True
    sender_name: Optional[str] = None


@router.post("/letters/draft")
def letter_draft(body: LetterIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    t0 = time.time()
    try:
        sender = {"name": body.sender_name} if body.sender_name else None
        res = letters.draft_letter(body.letter_type, body.fields, get_llm(), body.lang, body.use_ai, sender, body.privacy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    doc = GeneratedDocument(kind="letter", title=res["subject"], lang=body.lang, content=res["text"],
                            meta={"type": body.letter_type, "method": res["method"]})
    db.add(doc)
    db.commit()
    db.refresh(doc)
    orch.audit(db, actor, "LetterAgent", "draft_letter", None, {"type": body.letter_type, "document_id": doc.id},
               seconds=time.time() - t0, manual_minutes=MANUAL_MINUTES["letter"])
    return {**res, "id": doc.id}


class TextIn(BaseModel):
    text: str
    lang: str = "en"


@router.post("/letters/docx")
def letter_docx(body: TextIn):
    data = letters.letter_docx(body.text, body.lang)
    return StreamingResponse(io.BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             headers={"Content-Disposition": 'attachment; filename="official_letter.docx"'})


class MinutesIn(BaseModel):
    notes: str
    title: str = "Meeting Minutes"
    date: Optional[str] = None
    venue: str = ""
    attendees: List[str] = Field(default_factory=list)
    lang: str = "en"
    privacy: bool = True


@router.post("/minutes")
def minutes(body: MinutesIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    t0 = time.time()
    m = letters.draft_minutes(body.notes, body.model_dump(), get_llm(), body.lang, body.privacy)
    doc = GeneratedDocument(kind="minutes", title=m["title"], lang=body.lang, content=m["text"], meta={"method": m["method"]})
    db.add(doc)
    db.commit()
    db.refresh(doc)
    for a in m["actions"]:
        db.add(ActionItem(document_id=doc.id, action=a["action"], owner=a["owner"], deadline=a["deadline"]))
    db.commit()
    orch.audit(db, actor, "MinutesAgent", "draft_minutes", None, {"document_id": doc.id, "actions": len(m["actions"])},
               seconds=time.time() - t0, manual_minutes=MANUAL_MINUTES["minutes"])
    return {**m, "id": doc.id}


@router.get("/action-items")
def action_items(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ActionItem)
    if status:
        q = q.filter_by(status=status)
    return [{"id": a.id, "document_id": a.document_id, "action": a.action, "owner": a.owner, "deadline": a.deadline,
             "status": a.status} for a in q.order_by(ActionItem.id.desc()).all()]


class StatusIn(BaseModel):
    status: str


@router.patch("/action-items/{item_id}")
def update_action(item_id: int, body: StatusIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    if body.status not in ("open", "in_progress", "done"):
        raise HTTPException(400, "status must be open, in_progress or done")
    a = db.get(ActionItem, item_id)
    if not a:
        raise HTTPException(404, "Action item not found")
    a.status = body.status
    db.commit()
    orch.audit(db, actor, "MinutesAgent", "action_item_status", None, {"id": item_id, "status": body.status})
    return {"id": item_id, "status": a.status}


# ------------------------------------------------------------------------------------ 14. invoices
class InvoiceIn(BaseModel):
    partner: str
    country: Optional[str] = None
    direction: str = "provided"
    service_type: Optional[str] = None
    currency: str = "USD"
    exchange_rate: Optional[float] = None
    items: List[Dict]
    vat_pct: float = 0.0
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    terms_days: int = 30
    notes: Optional[str] = None


@router.get("/invoices/service-types")
def service_types():
    return invoices.SERVICE_TYPES


@router.post("/invoices")
def create_invoice(body: InvoiceIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    if not body.items:
        raise HTTPException(400, "At least one line item is required")
    inv = invoices.create_invoice(db, body.model_dump())
    orch.audit(db, actor, "InvoiceModule", "create_invoice", None, {"number": inv.number, "total": inv.total, "partner": inv.partner},
               manual_minutes=MANUAL_MINUTES["invoice"], seconds=0.2)
    return invoices.to_dict(inv)


@router.post("/invoices/import")
async def import_invoices(file: UploadFile = File(...), db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    t0 = time.time()
    try:
        df = read_table(await file.read(), file.filename or "invoices.csv")
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    res = invoices.import_dataframe(db, df)
    orch.audit(db, actor, "InvoiceModule", "import_invoices", None, {"created": res["created"], "skipped": len(res["skipped"])},
               rows=res["created"], seconds=time.time() - t0, manual_minutes=res["created"] * MANUAL_MINUTES["invoice"])
    return res


@router.get("/invoices")
def list_invoices(partner: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Invoice)
    if partner:
        q = q.filter(Invoice.partner == partner)
    out = [invoices.to_dict(i) for i in q.order_by(Invoice.id.desc()).all()]
    return [i for i in out if not status or i["effective_status"] == status]


@router.get("/invoices/summary")
def invoice_summary(db: Session = Depends(get_db)):
    return invoices.summary(db)


class InvStatusIn(BaseModel):
    status: str  # pending | submitted_to_accounts | paid
    date: Optional[str] = None


@router.patch("/invoices/{inv_id}/status")
def invoice_status(inv_id: int, body: InvStatusIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    if body.status not in invoices.VALID_STATUS:
        raise HTTPException(400, f"status must be one of {invoices.VALID_STATUS}")
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    d = body.date or date.today().isoformat()
    inv.status = body.status
    if body.status == "submitted_to_accounts":
        inv.submitted_date = d
    elif body.status == "paid":
        inv.paid_date = d
        inv.submitted_date = inv.submitted_date or d
    db.commit()
    orch.audit(db, actor, "InvoiceModule", "invoice_status", None, {"number": inv.number, "status": body.status})
    return invoices.to_dict(inv)


@router.get("/invoices/{inv_id}/html", response_class=HTMLResponse)
def invoice_html(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return invoices.invoice_html(inv)


@router.post("/invoices/{inv_id}/reminder")
def invoice_reminder(inv_id: int, lang: str = "en", use_ai: bool = True, db: Session = Depends(get_db),
                     actor: str = Depends(actor_of)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    fields = {"airline": inv.partner, "invoice_no": inv.number, "amount": f"{inv.total:,.2f}", "currency": inv.currency,
              "due_date": inv.due_date, "days_overdue": invoices.days_overdue(inv)}
    res = letters.draft_letter("payment_reminder", fields, get_llm(), lang, use_ai)
    orch.audit(db, actor, "InvoiceModule", "payment_reminder", None, {"number": inv.number}, manual_minutes=MANUAL_MINUTES["letter"])
    return res
