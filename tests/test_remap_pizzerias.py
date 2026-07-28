"""Tests for scripts/remap_pizzerias.py.

``scripts/`` is not a package, so the module is loaded directly from its
file path with importlib, the same pattern used for
tests/test_post_to_planfact.py.

Finding T1a: remap_pizzerias.py exists precisely to recompute pizzeria
detection when config.yaml's ``pizzeria_detection`` rules change, but it
called ``parse_ubl_xml`` without passing them, so it silently fell back to
the ubl_parser built-in defaults instead -- defeating its own purpose.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Invoice

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "remap_pizzerias.py"

_XML_NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
    'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:'
    'CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:'
    'CommonBasicComponents-2"'
)

# A hint that is NOT in ubl_parser.DEFAULT_PIZZERIA_PATTERNS, only in the
# custom config used below -- if the fix regresses, detection falls back to
# the built-in defaults and this hint matches nothing.
CUSTOM_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {_XML_NS}>
  <cbc:Note>Delivery via ZAGREB-CUSTOM-HINT</cbc:Note>
</Invoice>"""


def _load_module():
    spec = importlib.util.spec_from_file_location("remap_pizzerias", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    return _load_module()


def test_remap_uses_configured_pizzeria_patterns_not_builtin_defaults(module, tmp_path):
    (tmp_path / "inv1.xml").write_text(CUSTOM_XML, encoding="utf-8")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    inv = Invoice(document_nr="inv1", sender_oib="1", sender_name="s",
                  invoice_number="inv1", processing_status="parsed",
                  xml_path="inv1.xml", dodois_pizzeria=None)
    session.add(inv)
    session.commit()

    cfg = {
        "storage": {"xml_dir": str(tmp_path)},
        "pizzeria_detection": {"Zagreb-9": ["ZAGREB-CUSTOM-HINT"]},
    }

    with patch.object(module, "load_config", return_value=cfg), \
         patch.object(module, "get_engine", return_value=engine), \
         patch.object(module, "get_session_factory", return_value=Session):
        rc = module.main(True)

    assert rc == 0
    session.expire_all()
    updated = session.query(Invoice).filter_by(document_nr="inv1").one()
    assert updated.dodois_pizzeria == "Zagreb-9"


def test_remap_without_the_fix_would_detect_nothing(tmp_path):
    """Sanity check on the fixture itself: the custom hint really is absent
    from the built-in defaults, so a regression back to calling
    parse_ubl_xml(xml) with no patterns would leave dodois_pizzeria at None
    instead of failing loudly."""
    from app.core.ubl_parser import parse_ubl_xml
    ubl_default = parse_ubl_xml(CUSTOM_XML)
    assert ubl_default.delivery_pizzeria is None

    ubl_custom = parse_ubl_xml(CUSTOM_XML, {"Zagreb-9": ["ZAGREB-CUSTOM-HINT"]})
    assert ubl_custom.delivery_pizzeria == "Zagreb-9"
