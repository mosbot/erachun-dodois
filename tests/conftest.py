import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import (
    Base, SupplierMapping, DodoisSupplierCatalog,
    DodoisRawMaterialCatalog, ProductMapping, Invoice,
)
from app.core.ubl_parser import UBLInvoice, UBLLineItem
from datetime import datetime


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def metro_catalog(session):
    cat = DodoisSupplierCatalog(
        dodois_id="supplier-metro",
        dodois_name="METRO",
        dodois_inn="38016445738",
    )
    session.add(cat)
    session.flush()
    return cat


@pytest.fixture
def metro_mapping(session, metro_catalog):
    mapping = SupplierMapping(
        eracun_oib="38016445738",
        eracun_name="METRO Cash & Carry",
        dodois_catalog_id=metro_catalog.id,
        enabled=True,
    )
    session.add(mapping)
    session.flush()
    return mapping


@pytest.fixture
def jalapeno_material(session, metro_catalog):
    mat = DodoisRawMaterialCatalog(
        supplier_catalog_id=metro_catalog.id,
        dodois_material_id="mat-jalapeno",
        dodois_container_id="cont-jalapeno",
        dodois_name="Jalapeno 450g",
        unit=5,
        container_size=450.0,
    )
    session.add(mat)
    session.flush()
    return mat


@pytest.fixture
def jalapeno_pm(session, metro_mapping, jalapeno_material):
    pm = ProductMapping(
        supplier_mapping_id=metro_mapping.id,
        eracun_description="JALAPENO",
        dodois_raw_material_id=jalapeno_material.id,
        enabled=True,
    )
    session.add(pm)
    session.flush()
    return pm


def make_invoice(oib="38016445738", pizzeria="Zagreb-1", inv_number="TEST-001"):
    """Create an Invoice for tests (not yet added to session)."""
    inv = Invoice(
        electronic_id=None,
        document_nr=inv_number,
        sender_oib=oib,
        sender_name="METRO Cash & Carry",
        invoice_number=inv_number,
        issue_date=datetime(2026, 1, 28),
        dodois_pizzeria=pizzeria,
        processing_status="parsed",
    )
    return inv


def make_ubl(lines):
    ubl = UBLInvoice()
    ubl.invoice_number = "TEST-001"
    ubl.lines = lines
    return ubl
