"""SQLAlchemy models + session handling (SQLite by default, any SQL DB via DATABASE_URL)."""
from datetime import datetime
from sqlalchemy import (create_engine, Column, Integer, String, Float, Boolean, DateTime, Text,
                        ForeignKey, JSON)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Dataset(Base):
    __tablename__ = "datasets"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    original_filename = Column(String(300))
    n_rows = Column(Integer, default=0)
    n_cols = Column(Integer, default=0)
    current_version = Column(Integer, default=1)
    last_validation = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    versions = relationship("DatasetVersion", back_populates="dataset", cascade="all, delete-orphan")


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id = Column(Integer, primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), index=True)
    version = Column(Integer, nullable=False)
    path = Column(String(500), nullable=False)
    note = Column(String(300))
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    dataset = relationship("Dataset", back_populates="versions")


class Rule(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), index=True)
    text = Column(Text)
    rule_json = Column(JSON, nullable=False)
    source = Column(String(20), default="manual")  # manual | nl
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Correction(Base):
    __tablename__ = "corrections"
    id = Column(Integer, primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), index=True)
    version = Column(Integer)
    row_index = Column(Integer)
    column = Column(String(200))
    old_value = Column(Text)
    new_value = Column(Text)
    confidence = Column(Float)
    reason = Column(String(400))
    source = Column(String(20), default="rule")  # rule | ai
    status = Column(String(20), default="pending")  # pending|approved|rejected|applied
    decided_by = Column(String(100))
    decided_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String(100))
    agent = Column(String(60))
    action = Column(String(80), index=True)
    dataset_id = Column(Integer, index=True)
    details = Column(JSON)
    rows = Column(Integer, default=0)
    seconds = Column(Float, default=0.0)
    manual_minutes = Column(Float, default=0.0)


class GeneratedDocument(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    kind = Column(String(30), index=True)  # letter | minutes | report
    title = Column(String(300))
    lang = Column(String(5), default="en")
    content = Column(Text)
    meta = Column(JSON)
    dataset_id = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ActionItem(Base):
    __tablename__ = "action_items"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    action = Column(Text)
    owner = Column(String(120))
    deadline = Column(String(40))
    status = Column(String(20), default="open")  # open | in_progress | done
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    number = Column(String(40), unique=True, index=True)
    partner = Column(String(200), index=True)
    country = Column(String(80))
    direction = Column(String(20), default="provided")  # provided | received
    service_type = Column(String(120))
    currency = Column(String(5), default="USD")
    exchange_rate = Column(Float, default=1.0)  # 1 unit of currency -> BDT
    items = Column(JSON)
    subtotal = Column(Float, default=0.0)
    vat_pct = Column(Float, default=0.0)
    total = Column(Float, default=0.0)
    total_bdt = Column(Float, default=0.0)
    issue_date = Column(String(12))
    due_date = Column(String(12))
    status = Column(String(30), default="pending")  # pending|submitted_to_accounts|paid
    submitted_date = Column(String(12))
    paid_date = Column(String(12))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
