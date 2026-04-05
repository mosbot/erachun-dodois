"""
Database models for e-rachun - DodoIs.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, Enum,
    create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()


class Invoice(Base):
    """Incoming invoice from moj-eRačun."""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # eRačun identifiers
    electronic_id = Column(Integer, nullable=True, index=True)
    document_nr = Column(String(100), nullable=False)
    document_type_id = Column(Integer, default=1)
    document_type_name = Column(String(50), default="Račun")

    # eRačun status
    eracun_status_id = Column(Integer, default=0)
    eracun_status_name = Column(String(50), default="")
    imported = Column(Boolean, default=False)

    # Sender (supplier) info
    sender_oib = Column(String(20), nullable=False, index=True)
    sender_name = Column(String(255), nullable=False)
    sender_bu = Column(String(100), default="")

    # Invoice details (parsed from XML)
    invoice_number = Column(String(100), default="")
    issue_date = Column(DateTime, nullable=True, index=True)
    due_date = Column(DateTime, nullable=True)
    currency_code = Column(String(3), default="EUR")

    # Amounts
    total_without_vat = Column(Float, default=0.0)
    total_vat = Column(Float, default=0.0)
    total_with_vat = Column(Float, default=0.0)

    # File paths (relative to storage root)
    xml_path = Column(String(500), nullable=True)
    pdf_path = Column(String(500), nullable=True)

    # Processing status
    # new -> downloaded -> parsed -> uploaded_to_dodois -> error
    processing_status = Column(String(30), default="new", index=True)
    processing_error = Column(Text, nullable=True)

    # Dodois integration
    dodois_supply_id = Column(String(64), nullable=True)
    dodois_pizzeria = Column(String(50), nullable=True)

    # Timestamps from eRačun
    eracun_sent = Column(DateTime, nullable=True)
    eracun_delivered = Column(DateTime, nullable=True)
    eracun_updated = Column(DateTime, nullable=True)

    # Our timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Search index
    __table_args__ = (
        Index("ix_invoices_sender_date", "sender_name", "issue_date"),
        Index("ix_invoices_amount", "total_with_vat"),
    )

    def __repr__(self):
        return (
            f"<Invoice(id={self.id}, electronic_id={self.electronic_id}, "
            f"sender='{self.sender_name}', total={self.total_with_vat})>"
        )


class SyncLog(Base):
    """Log of sync operations."""
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")  # running, success, error
    invoices_found = Column(Integer, default=0)
    invoices_new = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)


def get_engine(database_url: str):
    return create_engine(database_url, echo=False, pool_pre_ping=True)


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(database_url: str):
    """Create all tables."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine
