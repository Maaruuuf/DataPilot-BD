"""Dataset endpoints: upload, profile, validate, rules, corrections, versions, pipeline, report, chat, audit."""
import io
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import MANUAL_MINUTES, MODEL_MAIN
from ..database import (get_db, Dataset, DatasetVersion, Rule, Correction, AuditLog, GeneratedDocument)
from ..core.dataio import read_table, save_df, version_path
from ..core.llm import get_llm
from ..agents import intake, validation, chat as chat_agent, extraction, orchestrator as orch

router = APIRouter()


def actor_of(x_user: Optional[str] = Header(None)) -> str:
    return x_user or "anonymous"


def get_ds(db: Session, ds_id: int) -> Dataset:
    ds = db.get(Dataset, ds_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds


def ds_dict(ds: Dataset) -> Dict:
    return {"id": ds.id, "name": ds.name, "rows": ds.n_rows, "columns": ds.n_cols, "version": ds.current_version,
            "created_at": ds.created_at.isoformat(), "last_validation": ds.last_validation}


def create_dataset(db: Session, df, name: str, filename: str, actor: str, note: str = "original upload") -> Dataset:
    ds = Dataset(name=name, original_filename=filename, n_rows=len(df), n_cols=df.shape[1], current_version=1)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    path = version_path(ds.id, 1)
    save_df(df, path)
    db.add(DatasetVersion(dataset_id=ds.id, version=1, path=str(path), created_by=actor, note=note))
    db.commit()
    return ds


# ------------------------------------------------------------------------------------------------ system
@router.get("/health")
def health():
    llm = get_llm()
    return {"status": "ok", "ai_enabled": llm.available, "model": MODEL_MAIN if llm.available else None}


# ------------------------------------------------------------------------------------------------ upload
@router.post("/datasets/upload")
async def upload(file: UploadFile = File(...), name: Optional[str] = Form(None), db: Session = Depends(get_db),
                 actor: str = Depends(actor_of)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        df = read_table(content, file.filename or "upload.csv")
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    if df.empty:
        raise HTTPException(400, "The file has no data rows")
    ds = create_dataset(db, df, name or (file.filename or "dataset").rsplit(".", 1)[0], file.filename, actor)
    profile = intake.profile_dataframe(df)
    orch.audit(db, actor, "IntakeAgent", "upload", ds.id, {"file": file.filename, "rows": len(df), "cols": df.shape[1]},
               rows=len(df), manual_minutes=len(df) * MANUAL_MINUTES["per_row_entry"] * 0.2)
    return {"dataset": ds_dict(ds), "profile": profile}


@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db)):
    return [ds_dict(d) for d in db.query(Dataset).order_by(Dataset.id.desc()).all()]


@router.get("/datasets/{ds_id}")
def get_dataset(ds_id: int, db: Session = Depends(get_db)):
    ds = get_ds(db, ds_id)
    versions = db.query(DatasetVersion).filter_by(dataset_id=ds_id).order_by(DatasetVersion.version).all()
    return {**ds_dict(ds), "versions": [{"version": v.version, "note": v.note, "created_by": v.created_by,
                                         "created_at": v.created_at.isoformat()} for v in versions]}


@router.delete("/datasets/{ds_id}")
def delete_dataset(ds_id: int, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    name = ds.name
    for model in (Rule, Correction):
        db.query(model).filter_by(dataset_id=ds_id).delete()
    db.delete(ds)
    db.commit()
    orch.audit(db, actor, "System", "delete_dataset", ds_id, {"name": name})
    return {"deleted": ds_id}


@router.get("/datasets/{ds_id}/preview")
def preview(ds_id: int, rows: int = 50, version: Optional[int] = None, db: Session = Depends(get_db)):
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds, version)
    return {"columns": list(df.columns), "rows": df.head(rows).to_dict("records"), "total": len(df)}


@router.get("/datasets/{ds_id}/profile")
def profile(ds_id: int, db: Session = Depends(get_db)):
    ds = get_ds(db, ds_id)
    return intake.profile_dataframe(orch.load_dataset_df(db, ds))


@router.get("/datasets/{ds_id}/download")
def download(ds_id: int, version: Optional[int] = None, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds, version)
    buf = io.BytesIO(df.to_csv(index=False).encode("utf-8-sig"))
    orch.audit(db, actor, "System", "download", ds_id, {"version": version or ds.current_version})
    return StreamingResponse(buf, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{ds.name}_v{version or ds.current_version}.csv"'})


# ------------------------------------------------------------------------------------------------ validation
class ValidateIn(BaseModel):
    use_ai: bool = False
    lang: str = "en"


@router.post("/datasets/{ds_id}/validate")
def validate(ds_id: int, body: ValidateIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    return orch.run_validation_step(db, ds, actor, get_llm(), body.use_ai, body.lang)


class NLRuleIn(BaseModel):
    text: str
    save: bool = False


@router.post("/datasets/{ds_id}/rules/nl")
def nl_rules(ds_id: int, body: NLRuleIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds)
    prof = intake.profile_dataframe(df)
    res = validation.nl_to_rules(body.text, df, prof, get_llm())
    if body.save and res["rules"]:
        for r in res["rules"]:
            db.add(Rule(dataset_id=ds_id, text=body.text, rule_json=r, source="nl"))
        db.commit()
        orch.audit(db, actor, "ValidationAgent", "save_rules", ds_id, {"text": body.text, "rules": len(res["rules"])})
    return res


@router.get("/datasets/{ds_id}/rules")
def list_rules(ds_id: int, db: Session = Depends(get_db)):
    return [{"id": r.id, "text": r.text, "rule": r.rule_json, "source": r.source, "active": r.active}
            for r in db.query(Rule).filter_by(dataset_id=ds_id).all()]


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(Rule, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()
    return {"deleted": rule_id}


# ------------------------------------------------------------------------------------------------ corrections
class SuggestIn(BaseModel):
    use_ai: bool = False
    lang: str = "en"
    privacy: bool = True


def corr_dict(c: Correction) -> Dict:
    return {"id": c.id, "row_index": c.row_index, "row_number": c.row_index + 2, "column": c.column,
            "old_value": c.old_value, "new_value": c.new_value, "confidence": c.confidence, "reason": c.reason,
            "source": c.source, "status": c.status, "decided_by": c.decided_by}


@router.post("/datasets/{ds_id}/corrections/suggest")
def suggest(ds_id: int, body: SuggestIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    rows = orch.suggest_step(db, ds, actor, get_llm(), body.use_ai, body.lang, body.privacy)
    return [corr_dict(c) for c in rows]


@router.get("/datasets/{ds_id}/corrections")
def list_corrections(ds_id: int, status: Optional[str] = None, db: Session = Depends(get_db)):
    ds = get_ds(db, ds_id)
    q = db.query(Correction).filter_by(dataset_id=ds_id, version=ds.current_version)
    if status:
        q = q.filter_by(status=status)
    return [corr_dict(c) for c in q.order_by(Correction.row_index).all()]


class DecisionIn(BaseModel):
    ids: List[int] = Field(default_factory=list)
    decision: str  # approved | rejected | pending
    min_confidence: Optional[float] = None  # bulk: approve everything >= this


@router.post("/datasets/{ds_id}/corrections/decide")
def decide(ds_id: int, body: DecisionIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    if body.decision not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "decision must be approved, rejected or pending")
    ds = get_ds(db, ds_id)
    q = db.query(Correction).filter_by(dataset_id=ds_id, version=ds.current_version).filter(Correction.status != "applied")
    if body.ids:
        q = q.filter(Correction.id.in_(body.ids))
    if body.min_confidence is not None:
        q = q.filter(Correction.confidence >= body.min_confidence)
    n = 0
    for c in q.all():
        c.status, c.decided_by, c.decided_at = body.decision, actor, datetime.utcnow()
        n += 1
    db.commit()
    orch.audit(db, actor, "HumanReview", f"corrections_{body.decision}", ds_id, {"count": n, "ids": body.ids[:50]})
    return {"updated": n}


@router.post("/datasets/{ds_id}/corrections/apply")
def apply(ds_id: int, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    ds = get_ds(db, ds_id)
    try:
        return orch.apply_step(db, ds, actor)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------------------------------------ pipeline / report / chat
class PipelineIn(BaseModel):
    use_ai: bool = False
    lang: str = "en"
    privacy: bool = True


@router.post("/datasets/{ds_id}/pipeline")
def pipeline(ds_id: int, body: PipelineIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    return orch.run_pipeline(db, get_ds(db, ds_id), actor, get_llm(), body.use_ai, body.lang, body.privacy)


class ReportIn(BaseModel):
    lang: str = "en"
    use_ai: bool = True
    extras: Dict = Field(default_factory=dict)


@router.post("/datasets/{ds_id}/report")
def make_report(ds_id: int, body: ReportIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    doc = orch.report_step(db, get_ds(db, ds_id), actor, get_llm(), body.lang, body.use_ai, body.extras)
    return {"id": doc.id, "title": doc.title, "ai": doc.meta.get("ai"), "html": doc.content}


@router.get("/documents/{doc_id}/html", response_class=HTMLResponse)
def document_html(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(GeneratedDocument, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc.content


@router.get("/documents")
def list_documents(kind: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(GeneratedDocument)
    if kind:
        q = q.filter_by(kind=kind)
    return [{"id": d.id, "kind": d.kind, "title": d.title, "lang": d.lang, "created_at": d.created_at.isoformat()}
            for d in q.order_by(GeneratedDocument.id.desc()).limit(100).all()]


class ChatIn(BaseModel):
    message: str
    history: List[Dict] = Field(default_factory=list)
    lang: str = "en"
    privacy: bool = True


@router.post("/datasets/{ds_id}/chat")
def chat(ds_id: int, body: ChatIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    import time
    t0 = time.time()
    ds = get_ds(db, ds_id)
    df = orch.load_dataset_df(db, ds)
    prof = intake.profile_dataframe(df)
    res = chat_agent.chat(df, prof, body.message, body.history, get_llm(), body.lang, body.privacy)
    orch.audit(db, actor, "ChatAgent", "chat", ds_id, {"question": body.message[:200], "tools": res["tool_calls"]},
               seconds=time.time() - t0, manual_minutes=8.0 if res["tool_calls"] else 0)
    return res


# ------------------------------------------------------------------------------------------------ smart entry
class ExtractIn(BaseModel):
    text: str
    schema_name: str = "invoice"
    custom_fields: List[str] = Field(default_factory=list)
    lang: str = "en"
    privacy: bool = True


@router.get("/extract/schemas")
def schemas():
    return {k: {f: {"type": t, "required": r} for f, (t, r) in v.items()} for k, v in extraction.SCHEMAS.items()}


@router.post("/extract")
def extract(body: ExtractIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    import time
    t0 = time.time()
    res = extraction.extract(body.text, body.schema_name, get_llm(), body.custom_fields, body.lang, body.privacy)
    orch.audit(db, actor, "ExtractionAgent", "extract", None, {"schema": body.schema_name, "records": len(res["rows"]),
                                                            "warnings": len(res["warnings"]), "method": res["method"]},
               rows=len(res["rows"]), seconds=time.time() - t0,
               manual_minutes=len(res["rows"]) * MANUAL_MINUTES["per_row_entry"] * 3)
    return res


class SaveRowsIn(BaseModel):
    rows: List[Dict]
    name: str
    append_to: Optional[int] = None


@router.post("/extract/save")
def save_rows(body: SaveRowsIn, db: Session = Depends(get_db), actor: str = Depends(actor_of)):
    import pandas as pd
    if not body.rows:
        raise HTTPException(400, "No rows to save")
    new = pd.DataFrame(body.rows).astype(str).replace({"None": "", "nan": ""})
    if body.append_to:
        ds = get_ds(db, body.append_to)
        old = orch.load_dataset_df(db, ds)
        merged = pd.concat([old, new.reindex(columns=old.columns, fill_value="")], ignore_index=True)
        v = ds.current_version + 1
        path = version_path(ds.id, v)
        save_df(merged, path)
        db.add(DatasetVersion(dataset_id=ds.id, version=v, path=str(path), created_by=actor,
                              note=f"{len(new)} rows added via Smart Entry"))
        ds.current_version, ds.n_rows, ds.last_validation = v, len(merged), None
        db.commit()
        orch.audit(db, actor, "ExtractionAgent", "append_rows", ds.id, {"added": len(new), "version": v})
        return {"dataset": ds_dict(ds)}
    ds = create_dataset(db, new, body.name, "smart_entry", actor, "created via Smart Entry")
    orch.audit(db, actor, "ExtractionAgent", "create_from_extraction", ds.id, {"rows": len(new)})
    return {"dataset": ds_dict(ds)}


# ------------------------------------------------------------------------------------------------ audit / stats
@router.get("/audit")
def audit_log(dataset_id: Optional[int] = None, limit: int = 200, db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if dataset_id:
        q = q.filter_by(dataset_id=dataset_id)
    return [{"id": a.id, "ts": a.ts.isoformat(timespec="seconds"), "actor": a.actor, "agent": a.agent, "action": a.action,
             "dataset_id": a.dataset_id, "details": a.details, "rows": a.rows, "seconds": a.seconds}
            for a in q.order_by(AuditLog.id.desc()).limit(limit).all()]


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    auto_s = db.query(func.coalesce(func.sum(AuditLog.seconds), 0.0)).scalar() or 0.0
    manual_m = db.query(func.coalesce(func.sum(AuditLog.manual_minutes), 0.0)).scalar() or 0.0
    saved_m = max(manual_m - auto_s / 60, 0)
    return {"datasets": db.query(Dataset).count(), "actions_logged": db.query(AuditLog).count(),
            "manual_minutes_estimate": round(manual_m, 1), "automated_seconds": round(auto_s, 1),
            "minutes_saved": round(saved_m, 1), "hours_saved": round(saved_m / 60, 2),
            "reduction_pct": round(100 * saved_m / manual_m, 1) if manual_m else 0.0}
