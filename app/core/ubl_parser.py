"""
UBL 2.1 Invoice XML parser.
Extracts key fields from Croatian eRačun XML (HR-CIUS standard).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from lxml import etree

logger = logging.getLogger(__name__)

# UBL 2.1 namespaces
NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cec": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}


@dataclass
class UBLLineItem:
    line_id: str = ""
    item_name: str = ""
    quantity: float = 0.0
    unit_code: str = ""
    unit_price: float = 0.0
    line_total: float = 0.0
    tax_percent: float = 0.0
    tax_amount: float = 0.0
    seller_item_id: str = ""
    buyer_item_id: str = ""
    standard_item_id: str = ""  # EAN/GTIN
    description: str = ""


@dataclass
class UBLInvoice:
    invoice_number: str = ""
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    currency_code: str = "EUR"

    # Supplier
    supplier_name: str = ""
    supplier_oib: str = ""
    supplier_address: str = ""

    # Buyer
    buyer_name: str = ""
    buyer_oib: str = ""

    # Totals
    total_without_vat: float = 0.0
    total_vat: float = 0.0
    total_with_vat: float = 0.0
    payable_amount: float = 0.0

    # Line items
    lines: list[UBLLineItem] = field(default_factory=list)

    # Raw PDF (base64) if embedded
    embedded_pdf_b64: Optional[str] = None


def parse_ubl_xml(xml_content: str | bytes) -> UBLInvoice:
    """Parse a UBL 2.1 Invoice XML into structured data."""
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")

    # Remove BOM if present
    if xml_content.startswith(b"\xef\xbb\xbf"):
        xml_content = xml_content[3:]

    root = etree.fromstring(xml_content)
    inv = UBLInvoice()

    # Invoice number
    inv.invoice_number = _text(root, ".//cbc:ID") or ""

    # Dates
    inv.issue_date = _parse_date(_text(root, ".//cbc:IssueDate"))
    inv.due_date = _parse_date(_text(root, ".//cbc:DueDate"))

    # Currency
    inv.currency_code = _text(root, ".//cbc:DocumentCurrencyCode") or "EUR"

    # Supplier (AccountingSupplierParty)
    supplier = root.find(".//cac:AccountingSupplierParty/cac:Party", NS)
    if supplier is not None:
        inv.supplier_name = (
            _text(supplier, ".//cac:PartyLegalEntity/cbc:RegistrationName")
            or _text(supplier, ".//cac:PartyName/cbc:Name")
            or ""
        )
        # Try multiple locations for supplier OIB
        raw_id = (
            _text(supplier, ".//cac:PartyLegalEntity/cbc:CompanyID")
            or _text(supplier, ".//cbc:EndpointID")
            or _text(supplier, ".//cac:PartyIdentification/cbc:ID")
            or ""
        )
        inv.supplier_oib = _clean_oib(raw_id)

        addr = supplier.find(".//cac:PostalAddress", NS)
        if addr is not None:
            parts = [
                _text(addr, "cbc:StreetName"),
                _text(addr, "cbc:CityName"),
                _text(addr, "cbc:PostalZone"),
            ]
            inv.supplier_address = ", ".join(p for p in parts if p)

    # Buyer (AccountingCustomerParty)
    buyer = root.find(".//cac:AccountingCustomerParty/cac:Party", NS)
    if buyer is not None:
        inv.buyer_name = (
            _text(buyer, ".//cac:PartyLegalEntity/cbc:RegistrationName")
            or _text(buyer, ".//cac:PartyName/cbc:Name")
            or ""
        )
        raw_id = (
            _text(buyer, ".//cac:PartyLegalEntity/cbc:CompanyID")
            or _text(buyer, ".//cbc:EndpointID")
            or _text(buyer, ".//cac:PartyIdentification/cbc:ID")
            or ""
        )
        inv.buyer_oib = _clean_oib(raw_id)

    # Totals
    monetary = root.find(".//cac:LegalMonetaryTotal", NS)
    if monetary is not None:
        inv.total_without_vat = _float(_text(monetary, "cbc:TaxExclusiveAmount"))
        inv.total_with_vat = _float(_text(monetary, "cbc:TaxInclusiveAmount"))
        inv.payable_amount = _float(_text(monetary, "cbc:PayableAmount"))

    # VAT total
    tax_total = root.find(".//cac:TaxTotal", NS)
    if tax_total is not None:
        inv.total_vat = _float(_text(tax_total, "cbc:TaxAmount"))

    # Line items
    for line_el in root.findall(".//cac:InvoiceLine", NS):
        line = UBLLineItem()
        line.line_id = _text(line_el, "cbc:ID") or ""
        line.quantity = _float(_text(line_el, "cbc:InvoicedQuantity"))
        qty_el = line_el.find("cbc:InvoicedQuantity", NS)
        if qty_el is not None:
            line.unit_code = qty_el.get("unitCode", "")
        line.line_total = _float(_text(line_el, "cbc:LineExtensionAmount"))

        # Price
        price_el = line_el.find(".//cac:Price/cbc:PriceAmount", NS)
        if price_el is not None:
            line.unit_price = _float(price_el.text)

        # Item details
        item = line_el.find(".//cac:Item", NS)
        if item is not None:
            line.item_name = _text(item, "cbc:Name") or ""
            line.description = _text(item, "cbc:Description") or ""

            # Seller item ID
            sid = item.find(".//cac:SellersItemIdentification/cbc:ID", NS)
            if sid is not None:
                line.seller_item_id = sid.text or ""

            # Buyer item ID
            bid = item.find(".//cac:BuyersItemIdentification/cbc:ID", NS)
            if bid is not None:
                line.buyer_item_id = bid.text or ""

            # Standard (EAN/GTIN)
            std = item.find(".//cac:StandardItemIdentification/cbc:ID", NS)
            if std is not None:
                line.standard_item_id = std.text or ""

            # Tax
            tax_cat = item.find(".//cac:ClassifiedTaxCategory", NS)
            if tax_cat is not None:
                line.tax_percent = _float(_text(tax_cat, "cbc:Percent"))

        # Tax amount on line
        line_tax = line_el.find(".//cac:TaxTotal/cbc:TaxAmount", NS)
        if line_tax is not None:
            line.tax_amount = _float(line_tax.text)

        inv.lines.append(line)

    # Embedded PDF (AdditionalDocumentReference with mimeCode application/pdf)
    for doc_ref in root.findall(".//cac:AdditionalDocumentReference", NS):
        attach = doc_ref.find(".//cac:Attachment/cbc:EmbeddedDocumentBinaryObject", NS)
        if attach is not None:
            mime = attach.get("mimeCode", "")
            if "pdf" in mime.lower():
                inv.embedded_pdf_b64 = attach.text
                break

    return inv


def _clean_oib(raw: str) -> str:
    """Extract clean OIB from various formats: 'HR38016445738', '9934:38016445738', etc."""
    if not raw:
        return ""
    # Remove scheme prefix "9934:"
    if ":" in raw:
        raw = raw.split(":")[-1]
    # Remove country prefix "HR"
    if raw.upper().startswith("HR"):
        raw = raw[2:]
    return raw.strip()


def _text(el, xpath: str) -> Optional[str]:
    """Get text of first matching element."""
    found = el.find(xpath, NS)
    if found is not None and found.text:
        return found.text.strip()
    return None


def _float(val: Optional[str]) -> float:
    if not val:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_date(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d")
    except ValueError:
        return None
