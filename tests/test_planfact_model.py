from datetime import datetime

from app.db.models import Invoice


def test_invoice_has_planfact_columns(session):
    inv = Invoice(
        document_nr="X-1", sender_oib="25531986377", sender_name="Wolt Zagreb d.o.o.",
        invoice_number="X-1", processing_status="parsed",
        planfact_operation_id="283549770",
        planfact_external_id="87588-2553198637711-2026",
        planfact_posted_at=datetime(2026, 7, 6, 8, 0, 0),
        planfact_error=None,
    )
    session.add(inv)
    session.commit()

    stored = session.query(Invoice).filter_by(document_nr="X-1").one()
    assert stored.planfact_operation_id == "283549770"
    assert stored.planfact_external_id == "87588-2553198637711-2026"
    assert stored.planfact_posted_at.year == 2026
    assert stored.planfact_error is None


def test_planfact_fields_default_to_none(session):
    inv = Invoice(document_nr="X-2", sender_oib="1", sender_name="s",
                  invoice_number="X-2", processing_status="parsed")
    session.add(inv)
    session.commit()
    stored = session.query(Invoice).filter_by(document_nr="X-2").one()
    assert stored.planfact_operation_id is None
    assert stored.planfact_error is None
