"""Tests for UBL parsing of Invoice vs CreditNote documents."""

from app.core.ubl_parser import parse_ubl_xml

CN_NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2" '
    'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:'
    'CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:'
    'CommonBasicComponents-2"'
)
INV_NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
    'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:'
    'CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:'
    'CommonBasicComponents-2"'
)


def _credit_note(sign: int = 1) -> str:
    """Build a CreditNote whose amounts carry the given sign.

    sign=+1 reproduces STANIĆ / Inter Alfa (UBL-correct: positive amounts,
    sign implied by the document type). sign=-1 reproduces Pivac / METRO,
    which pre-sign the amounts in the XML.
    """
    def a(v):
        return f"{sign * v:.2f}"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<CreditNote {CN_NS}>
  <cbc:ID>28489/V850/900</cbc:ID>
  <cbc:IssueDate>2026-06-09</cbc:IssueDate>
  <cbc:CreditNoteTypeCode>381</cbc:CreditNoteTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyLegalEntity>
      <cbc:RegistrationName>STANIĆ d.o.o.</cbc:RegistrationName>
      <cbc:CompanyID>HR12345678901</cbc:CompanyID>
    </cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:TaxTotal>
    <cbc:TaxAmount>{a(216.95)}</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount>{a(800.00)}</cbc:TaxableAmount>
      <cbc:TaxAmount>{a(200.00)}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>25</cbc:Percent></cac:TaxCategory>
    </cac:TaxSubtotal>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount>{a(85.77)}</cbc:TaxableAmount>
      <cbc:TaxAmount>{a(16.95)}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>13</cbc:Percent></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount>{a(885.77)}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount>{a(1102.72)}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount>{a(1102.72)}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:CreditNoteLine>
    <cbc:ID>1</cbc:ID>
    <cbc:CreditedQuantity unitCode="KGM">10</cbc:CreditedQuantity>
    <cbc:LineExtensionAmount>{a(885.77)}</cbc:LineExtensionAmount>
    <cac:TaxTotal><cbc:TaxAmount>{a(216.95)}</cbc:TaxAmount></cac:TaxTotal>
    <cac:Item>
      <cbc:Name>PIZZA SUNKA REZANA</cbc:Name>
      <cac:StandardItemIdentification>
        <cbc:ID>3850102030405</cbc:ID>
      </cac:StandardItemIdentification>
      <cac:ClassifiedTaxCategory><cbc:Percent>25</cbc:Percent></cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price><cbc:PriceAmount>88.577</cbc:PriceAmount></cac:Price>
  </cac:CreditNoteLine>
</CreditNote>"""


PLAIN_INVOICE = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {INV_NS}>
  <cbc:ID>35415/V850/900</cbc:ID>
  <cbc:IssueDate>2026-07-21</cbc:IssueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cac:TaxTotal>
    <cbc:TaxAmount>112.93</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount>463.72</cbc:TaxableAmount>
      <cbc:TaxAmount>112.93</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>25</cbc:Percent></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount>463.72</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount>576.65</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount>576.65</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="KGM">5</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount>463.72</cbc:LineExtensionAmount>
    <cac:TaxTotal><cbc:TaxAmount>112.93</cbc:TaxAmount></cac:TaxTotal>
    <cac:Item><cbc:Name>PIZZA SUNKA REZANA</cbc:Name></cac:Item>
    <cac:Price><cbc:PriceAmount>92.744</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>"""


# ── Credit note detection ────────────────────────────────────────────────────

def test_credit_note_is_flagged():
    ubl = parse_ubl_xml(_credit_note())
    assert ubl.is_credit_note is True
    assert ubl.document_type_code == "381"


def test_plain_invoice_is_not_flagged():
    ubl = parse_ubl_xml(PLAIN_INVOICE)
    assert ubl.is_credit_note is False


# ── Sign normalisation ───────────────────────────────────────────────────────

def test_credit_note_with_positive_amounts_is_negated():
    """STANIĆ / Inter Alfa case — the reported bug."""
    ubl = parse_ubl_xml(_credit_note(sign=1))
    assert ubl.total_without_vat == -885.77
    assert ubl.total_vat == -216.95
    assert ubl.total_with_vat == -1102.72
    assert ubl.payable_amount == -1102.72


def test_credit_note_with_negative_amounts_stays_negative():
    """Pivac / METRO already pre-sign their XML — must not be flipped back."""
    ubl = parse_ubl_xml(_credit_note(sign=-1))
    assert ubl.total_without_vat == -885.77
    assert ubl.total_vat == -216.95
    assert ubl.total_with_vat == -1102.72


def test_credit_note_tax_subtotals_are_negative():
    ubl = parse_ubl_xml(_credit_note(sign=1))
    by_rate = {s["percent"]: s for s in ubl.tax_subtotals}
    assert by_rate[25.0]["taxable"] == -800.00
    assert by_rate[25.0]["tax"] == -200.00
    assert by_rate[13.0]["taxable"] == -85.77
    assert by_rate[13.0]["tax"] == -16.95


def test_credit_note_totals_reconcile_with_tax_subtotals():
    ubl = parse_ubl_xml(_credit_note(sign=1))
    assert round(sum(s["tax"] for s in ubl.tax_subtotals), 2) == ubl.total_vat


def test_plain_invoice_signs_untouched():
    ubl = parse_ubl_xml(PLAIN_INVOICE)
    assert ubl.total_without_vat == 463.72
    assert ubl.total_vat == 112.93
    assert ubl.total_with_vat == 576.65
    assert ubl.tax_subtotals[0]["taxable"] == 463.72


# ── CreditNoteLine parsing ───────────────────────────────────────────────────

def test_credit_note_lines_are_parsed():
    ubl = parse_ubl_xml(_credit_note(sign=1))
    assert len(ubl.lines) == 1
    line = ubl.lines[0]
    assert line.item_name == "PIZZA SUNKA REZANA"
    assert line.standard_item_id == "3850102030405"
    assert line.unit_code == "KGM"
    assert line.tax_percent == 25.0


def test_credit_note_line_amounts_are_negative():
    ubl = parse_ubl_xml(_credit_note(sign=1))
    line = ubl.lines[0]
    assert line.line_total == -885.77
    assert line.tax_amount == -216.95
    assert line.quantity == -10.0
    # Unit price is a rate, not an amount — it stays positive.
    assert line.unit_price == 88.577


def test_credit_note_line_amounts_negative_for_presigned_supplier():
    ubl = parse_ubl_xml(_credit_note(sign=-1))
    line = ubl.lines[0]
    assert line.line_total == -885.77
    assert line.tax_amount == -216.95
    assert line.quantity == -10.0


def test_credit_note_line_totals_reconcile_with_document_total():
    ubl = parse_ubl_xml(_credit_note(sign=1))
    assert round(sum(l.line_total for l in ubl.lines), 2) == ubl.total_without_vat


def test_plain_invoice_lines_still_parse():
    ubl = parse_ubl_xml(PLAIN_INVOICE)
    assert len(ubl.lines) == 1
    line = ubl.lines[0]
    assert line.item_name == "PIZZA SUNKA REZANA"
    assert line.quantity == 5.0
    assert line.line_total == 463.72
    assert line.tax_amount == 112.93
