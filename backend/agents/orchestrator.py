"""Orchestrator: runs the agents in order and writes every step to the SQL audit trail (Feature 9)."""
import time
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
from sqlalchemy.orm import Session

from ..config import MANUAL_MINUTES
from ..database import (AuditLog, Dataset, DatasetVersion, Correction, Rule, GeneratedDocument)
from ..core.dataio import load_df, save_df, version_path
from ..core.llm import LLM
from . import intake, validation, correction, reporting


def audit(db: Session, actor: str, agent: str, action: str, dataset_id: Optional[int] = None, details: Optional[Dict] = None,
          rows: int = 0, seconds: float = 0.0, manual_minutes: float = 0.0) -> None:
    db.add(AuditLog(actor=actor or "anonymous", agent=agent, action=action, dataset_id=dataset_id, details=details or {},
                    rows=rows, seconds=round(seconds, 3), manual_minutes=manual_minutes))
    db.commit()


def get_version(db: Session, ds: Dataset, version: Optional[int] = None) -> DatasetVersion:
    v = version or ds.current_version
    ver = db.query(DatasetVersion).filter_by(dataset_id=ds.id, version=v).first()
    if not ver:
        raise ValueError(f"Version {v} not found")
    return ver


def load_dataset_df(db: Session, ds: Dataset, version: Optional[int] = None) -> pd.DataFrame:
    return load_df(get_version(db, ds, version).path)


def custom_rules(db: Session, ds_id: int) -> List[Dict]:
    return [r.rule_json for r in db.query(Rule).filter_by(dataset_id=ds_id, active=True).all()]


def run_validation_step(db: Session, ds: Dataset, actor: str, llm: LLM, use_ai: bool, lang: str) -> Dict:
    t0 = time.time()
    df = load_dataset_df(db, ds)
    profile = intake.profile_dataframe(df)
    res = validation.run_validation(df, profile, custom_rules(db, ds.id), llm, use_ai, lang)
    res["profile"] = profile
    res["version"] = ds.current_version
    res["ai_used"] = bool(use_ai and llm.available)
    ds.last_validation = {"score": res["score"], "summary": res["summary"], "version": ds.current_version,
                          "issue_count": len(res["issues"]), "at": datetime.utcnow().isoformat()}
    db.commit()
    audit(db, actor, "ValidationAgent", "validate", ds.id,
          {"issues": len(res["issues"]), "score": res["score"]["overall"], "ai": res["ai_used"]},
          rows=len(df), seconds=time.time() - t0, manual_minutes=len(df) * MANUAL_MINUTES["per_row_validation"])
    return res


def suggest_step(db: Session, ds: Dataset, actor: str, llm: LLM, use_ai: bool, lang: str, privacy: bool,
                 validation_result: Optional[Dict] = None) -> List[Correction]:
    t0 = time.time()
    df = load_dataset_df(db, ds)
    profile = intake.profile_dataframe(df)
    val = validation_result or validation.run_validation(df, profile, custom_rules(db, ds.id), llm, use_ai, lang)
    sugg = correction.suggest_corrections(df, val["issues"], profile, llm, use_ai, lang, privacy)
    # replace previous pending suggestions for this version
    db.query(Correction).filter_by(dataset_id=ds.id, version=ds.current_version, status="pending").delete()
    rows = [Correction(dataset_id=ds.id, version=ds.current_version, **s) for s in sugg]
    db.add_all(rows)
    db.commit()
    audit(db, actor, "CorrectionAgent", "suggest_corrections", ds.id, {"suggestions": len(rows)},
          rows=len(df), seconds=time.time() - t0)
    return rows


def apply_step(db: Session, ds: Dataset, actor: str) -> Dict:
    """Apply all approved corrections -> new dataset version (original is never overwritten)."""
    t0 = time.time()
    approved = db.query(Correction).filter_by(dataset_id=ds.id, version=ds.current_version, status="approved").all()
    if not approved:
        raise ValueError("No approved corrections to apply")
    df = load_dataset_df(db, ds)
    payload = [{"row_index": c.row_index, "column": c.column, "new_value": c.new_value} for c in approved]
    new_df = correction.apply_corrections(df, payload)
    new_v = ds.current_version + 1
    path = version_path(ds.id, new_v)
    save_df(new_df, path)
    db.add(DatasetVersion(dataset_id=ds.id, version=new_v, path=str(path), created_by=actor,
                          note=f"{len(approved)} approved corrections applied"))
    for c in approved:
        c.status = "applied"
    ds.current_version, ds.n_rows = new_v, len(new_df)
    ds.last_validation = None
    db.commit()
    audit(db, actor, "CorrectionAgent", "apply_corrections", ds.id,
          {"applied": len(approved), "new_version": new_v, "rows_before": len(df), "rows_after": len(new_df)},
          rows=len(approved), seconds=time.time() - t0)
    return {"new_version": new_v, "applied": len(approved), "rows": len(new_df)}


def report_step(db: Session, ds: Dataset, actor: str, llm: LLM, lang: str, use_ai: bool,
                extras: Optional[Dict] = None) -> GeneratedDocument:
    t0 = time.time()
    df = load_dataset_df(db, ds)
    profile = intake.profile_dataframe(df)
    val = validation.run_validation(df, profile, custom_rules(db, ds.id), llm, False, lang)
    narrative = reporting.ai_narrative(profile, val, extras or {}, llm if use_ai else _OFFLINE, lang)
    html = reporting.build_html(ds.name, profile, val, narrative, lang, extras or {})
    doc = GeneratedDocument(kind="report", title=f"{ds.name} - report v{ds.current_version}", lang=lang, content=html,
                            meta={"score": val["score"], "ai": bool(use_ai and llm.available)}, dataset_id=ds.id)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    audit(db, actor, "ReportingAgent", "generate_report", ds.id, {"document_id": doc.id, "lang": lang},
          rows=len(df), seconds=time.time() - t0, manual_minutes=MANUAL_MINUTES["report"])
    return doc


class _Offline:
    available = False


_OFFLINE = _Offline()


def run_pipeline(db: Session, ds: Dataset, actor: str, llm: LLM, use_ai: bool, lang: str, privacy: bool) -> Dict:
    """One click: Intake -> Validation -> Correction suggestions -> Report."""
    t0 = time.time()
    steps = []
    val = run_validation_step(db, ds, actor, llm, use_ai, lang)
    steps.append({"agent": "Intake + Validation", "issues": len(val["issues"]), "score": val["score"]["overall"]})
    sugg = suggest_step(db, ds, actor, llm, use_ai, lang, privacy, val)
    steps.append({"agent": "Correction", "suggestions": len(sugg),
                  "high_confidence": sum(1 for s in sugg if s.confidence >= 0.9)})
    doc = report_step(db, ds, actor, llm, lang, use_ai)
    steps.append({"agent": "Reporting", "document_id": doc.id})
    audit(db, actor, "Orchestrator", "pipeline", ds.id, {"steps": steps}, rows=ds.n_rows, seconds=time.time() - t0)
    return {"steps": steps, "score": val["score"], "summary": val["summary"], "report_id": doc.id,
            "suggestions": len(sugg), "seconds": round(time.time() - t0, 2)}
