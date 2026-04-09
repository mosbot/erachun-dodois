"""
Database models for e-rachun - DodoIs.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, Index, create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()


# ============================================================
# Invoice & SyncLog (existing)
# ============================================================

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

    # Processing status: new -> downloaded -> parsed -> uploaded_to_dodois -> error
    processing_status = Column(String(30), default="new", index=True)
    processing_error = Column(Text, nullable=True)

    # Dodois integration
    dodois_supply_id = Column(String(64), nullable=True)
    dodois_pizzeria = Column(String(50), nullable=True)
    dodois_upload_partial = Column(Boolean, default=False)
    dodois_skipped_count = Column(Integer, default=0)
    dodois_skipped_lines = Column(Text, nullable=True)  # JSON list of skipped descriptions

    # Timestamps from eRačun
    eracun_sent = Column(DateTime, nullable=True)
    eracun_delivered = Column(DateTime, nullable=True)
    eracun_updated = Column(DateTime, nullable=True)

    # Our timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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


# ============================================================
# Dodois catalog & mapping (new — Stage 2)
# ============================================================

class DodoisSupplierCatalog(Base):
    """Cached list of suppliers from Dodois API."""
    __tablename__ = "dodois_supplier_catalog"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    dodois_id   = Column(String(64), unique=True, nullable=False, index=True)
    dodois_name = Column(String(255), nullable=False)
    dodois_inn  = Column(String(50), nullable=True)   # OIB/INN as stored in Dodois
    synced_at   = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    raw_materials   = relationship("DodoisRawMaterialCatalog", back_populates="supplier")
    supplier_mappings = relationship("SupplierMapping", back_populates="dodois_supplier")

    def __repr__(self):
        return f"<DodoisSupplierCatalog(id={self.id}, name='{self.dodois_name}')>"


class SupplierMapping(Base):
    """Maps an eRačun supplier (by OIB) to a Dodois supplier.
    Rows are auto-created when a new supplier appears in an invoice.
    """
    __tablename__ = "supplier_mappings"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    eracun_oib        = Column(String(20), unique=True, nullable=False, index=True)
    eracun_name       = Column(String(255), nullable=False)  # display name from latest invoice
    dodois_catalog_id = Column(Integer, ForeignKey("dodois_supplier_catalog.id"), nullable=True)
    enabled           = Column(Boolean, default=False)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    dodois_supplier  = relationship("DodoisSupplierCatalog", back_populates="supplier_mappings")
    product_mappings = relationship("ProductMapping", back_populates="supplier_mapping",
                                   cascade="all, delete-orphan")

    @property
    def status(self) -> str:
        if self.dodois_catalog_id is None:
            return "unmapped"
        return "enabled" if self.enabled else "disabled"

    def __repr__(self):
        return f"<SupplierMapping(oib='{self.eracun_oib}', name='{self.eracun_name}')>"


class DodoisRawMaterialCatalog(Base):
    """Raw materials / goods per Dodois supplier, cached from Dodois API."""
    __tablename__ = "dodois_raw_material_catalog"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    supplier_catalog_id = Column(Integer, ForeignKey("dodois_supplier_catalog.id"), nullable=False)
    dodois_material_id  = Column(String(64), nullable=False)
    dodois_container_id = Column(String(64), nullable=True)   # null for pcs-only items (e.g. Vindi Sok)
    dodois_name         = Column(String(255), nullable=False)
    unit                = Column(Integer, nullable=False)      # 1=pcs, 5=g/ml, 8=m
    container_size      = Column(Float, nullable=False)        # package size in unit
    synced_at           = Column(DateTime, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)

    supplier         = relationship("DodoisSupplierCatalog", back_populates="raw_materials")
    product_mappings = relationship("ProductMapping", back_populates="raw_material")

    def __repr__(self):
        return f"<DodoisRawMaterialCatalog(name='{self.dodois_name}', unit={self.unit}, size={self.container_size})>"


class ProductMapping(Base):
    """Maps an eRačun invoice line (by description/EAN) to a Dodois raw material.
    Created manually via UI or auto-matched on import.
    """
    __tablename__ = "product_mappings"

    id                     = Column(Integer, primary_key=True, autoincrement=True)
    supplier_mapping_id    = Column(Integer, ForeignKey("supplier_mappings.id"), nullable=False)
    eracun_description     = Column(String(500), nullable=False)  # text from invoice line
    eracun_ean             = Column(String(30), nullable=True)     # EAN barcode if present
    dodois_raw_material_id = Column(Integer, ForeignKey("dodois_raw_material_catalog.id"), nullable=True)
    enabled                = Column(Boolean, default=True)
    created_at             = Column(DateTime, default=datetime.utcnow)
    updated_at             = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier_mapping = relationship("SupplierMapping", back_populates="product_mappings")
    raw_material     = relationship("DodoisRawMaterialCatalog", back_populates="product_mappings")

    def __repr__(self):
        return f"<ProductMapping(desc='{self.eracun_description[:40]}')>"


# ============================================================
# Seed data
# ============================================================

# METRO raw materials from CLAUDE.md
_METRO_RAW_MATERIALS = [
    # (dodois_material_id, dodois_container_id, name, unit, container_size)
    ("11f04c6c20e7689dab59d130df32c874", None,                                 "Vindi Sok 0.25L",       1, 1.0),
    ("11eef67e9f071a84b9bf035189a8c3b4", "11ef21a9a6d6257646ae4a288c274c30",  "Jalapeno 450g",         5, 450.0),
    ("11ef598b10a74749b2f2681d5eb6ed44", "11ef598ba73759a5f13bdee407eca450",  "Corn Flour 1kg",        5, 1000.0),
    ("11ef63950a77c4a8940f1b449952238b", "11ef6395b629bb5a214723fe063ca790",  "White sauce 1L",        5, 1000.0),
    ("11f0e250ce6ee2c5832a9c090a6d8427", "11f0e250be46b7cb367cecb2c00e5070",  "Sour cream 180g",       5, 180.0),
    ("11f064be7ddf3c9aad83e7d48b8ff087", "11f064bebc0d9500c9116f177a9feb30",  "Cheesecake 1250g",      1, 10.0),
    ("11ef1f371be880cb9bb51697e04b8e98", "11ef20fdaa55899b7337ef819c841010",  "Cheddar 1kg",           5, 1000.0),
    ("11eeeb8cf15b1d408deca6d020856fe5", "11ef2114843b372d94e5d8b36e9d2670",  "Blue Cheese 500g",      5, 500.0),
    ("11f0801343f5d99a88b57718f762fc99", "11f080138232457cadcce61b3f41dba0",  "Plastic bag 2L",        1, 25.0),
    ("11ef59532de07eaea59d5fa9fa497c45", "11ef595389b27dc8999d655923361800",  "Parmegiano 500g",       5, 500.0),
    ("11f09e01e6cdf0628af93a405cd35683", "11f09e018cdf7bb30ddfe358d5033b00",  "Black Olives 935g",     5, 935.0),
    ("11f003ea671ff4d2b15b238cb59cef6b", "11f003e8aa0583db93718fcc52e94280",  "Napkins 500pcs",        1, 500.0),
    ("11f064c02b3f45288220ae67f82dbd7c", "11f064c0962369a37ea1d8f424f0baa0",  "Paper plate 50pcs",     1, 50.0),
    ("11f0ad16c3e6a934ad48e59b9522af90", "11f0ad16b819997cbcd56d3dbffe1170",  "Plastic bag 100pcs",    1, 100.0),
    ("11f04c6ce021468f916d5998122c3167", "11f04c6ca01ac1708f541944dd2b0b50",  "Baking paper 8m",       8, 8.0),
]


def seed_all(session, cfg: dict) -> None:
    """Seed all catalog tables from config.yaml on first run. Idempotent."""
    _seed_supplier_catalog(session, cfg)
    _seed_supplier_mappings(session, cfg)
    _seed_raw_material_catalog(session)


def _seed_supplier_catalog(session, cfg: dict) -> None:
    if session.query(DodoisSupplierCatalog).count() > 0:
        return
    for key, s in cfg.get("dodois_suppliers", {}).items():
        did = s.get("dodois_supplier_id")
        if not did:
            continue
        entry = DodoisSupplierCatalog(
            dodois_id=did,
            dodois_name=s.get("dodois_name", key),
            dodois_inn=s.get("eracun_oib") or s.get("oib") or None,
        )
        session.add(entry)
    session.commit()


def _seed_supplier_mappings(session, cfg: dict) -> None:
    if session.query(SupplierMapping).count() > 0:
        return
    for key, s in cfg.get("dodois_suppliers", {}).items():
        oib = s.get("eracun_oib") or s.get("oib")
        if not oib:
            continue
        did = s.get("dodois_supplier_id")
        catalog = (
            session.query(DodoisSupplierCatalog).filter_by(dodois_id=did).first()
            if did else None
        )
        mapping = SupplierMapping(
            eracun_oib=oib,
            eracun_name=s.get("eracun_name") or s.get("dodois_name", key),
            dodois_catalog_id=catalog.id if catalog else None,
            enabled=s.get("enabled", False),
        )
        session.add(mapping)
    session.commit()


def _seed_raw_material_catalog(session) -> None:
    if session.query(DodoisRawMaterialCatalog).count() > 0:
        return
    metro = session.query(DodoisSupplierCatalog).filter_by(
        dodois_id="11eeeb8be458f06caf0d5b3908d3a4aa"
    ).first()
    if not metro:
        return
    for mat_id, cont_id, name, unit, size in _METRO_RAW_MATERIALS:
        session.add(DodoisRawMaterialCatalog(
            supplier_catalog_id=metro.id,
            dodois_material_id=mat_id,
            dodois_container_id=cont_id,
            dodois_name=name,
            unit=unit,
            container_size=size,
        ))
    session.commit()


# ============================================================
# DB helpers
# ============================================================

def get_or_create_supplier_mapping(session, eracun_oib: str, eracun_name: str) -> SupplierMapping:
    """Return existing mapping or create a new unmapped one."""
    mapping = session.query(SupplierMapping).filter_by(eracun_oib=eracun_oib).first()
    if not mapping:
        mapping = SupplierMapping(
            eracun_oib=eracun_oib,
            eracun_name=eracun_name,
            enabled=False,
        )
        session.add(mapping)
        session.commit()
    elif mapping.eracun_name != eracun_name:
        # Keep the name up to date
        mapping.eracun_name = eracun_name
        session.commit()
    return mapping


def sync_product_mappings_from_lines(session, supplier_mapping: SupplierMapping, ubl_lines: list) -> int:
    """Create ProductMapping rows for any new line descriptions seen in an invoice.
    Returns count of newly created rows.
    """
    new_count = 0
    for line in ubl_lines:
        desc = (line.item_name or line.description or "").strip()
        if not desc:
            continue
        ean = (line.standard_item_id or "").strip() or None
        existing = (
            session.query(ProductMapping)
            .filter_by(supplier_mapping_id=supplier_mapping.id, eracun_description=desc)
            .first()
        )
        if not existing:
            session.add(ProductMapping(
                supplier_mapping_id=supplier_mapping.id,
                eracun_description=desc,
                eracun_ean=ean,
            ))
            new_count += 1
        elif ean and not existing.eracun_ean:
            existing.eracun_ean = ean
    if new_count:
        session.commit()
    return new_count


def get_product_mapping(session, supplier_mapping_id: int, description: str, ean: str = None):
    """Find product mapping by EAN (exact) or description (case-insensitive substring)."""
    if ean:
        result = (
            session.query(ProductMapping)
            .filter_by(supplier_mapping_id=supplier_mapping_id, eracun_ean=ean, enabled=True)
            .first()
        )
        if result:
            return result
    return (
        session.query(ProductMapping)
        .filter(
            ProductMapping.supplier_mapping_id == supplier_mapping_id,
            ProductMapping.enabled == True,
            ProductMapping.eracun_description.ilike(f"%{description}%"),
        )
        .first()
    )


def is_dodois_supplier_enabled(session, eracun_oib: str) -> bool:
    """Check if the supplier is mapped and enabled for Dodois upload."""
    mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=eracun_oib, enabled=True
    ).first()
    return mapping is not None and mapping.dodois_catalog_id is not None


def get_engine(database_url: str):
    return create_engine(database_url, echo=False, pool_pre_ping=True)


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(database_url: str):
    """Create all tables and return engine."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine
