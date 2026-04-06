"""
Invoice synchronization service.
Fetches new invoices from eRačun, parses XML, extracts PDF, saves to DB.
"""

import base64
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.eracun_client import EracunClient, InboxItem
from app.core.ubl_parser import parse_ubl_xml
from app.db.models import Invoice, SyncLog, get_or_create_supplier_mapping, sync_product_mappings_from_lines

logger = logging.getLogger(__name__)


class InvoiceSyncService:
    """Sync invoices from eRačun to local database."""

    def __init__(
        self,
        eracun_client: EracunClient,
        session_factory,
        pdf_dir: str = "/app/data/pdfs",
        xml_dir: str = "/app/data/xmls",
    ):
        self.eracun = eracun_client
        self.session_factory = session_factory
        self.pdf_dir = Path(pdf_dir)
        self.xml_dir = Path(xml_dir)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.xml_dir.mkdir(parents=True, exist_ok=True)

    def sync(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        lookback_days: int = 90,
    ) -> dict:
        """
        Full sync: query inbox -> download new -> parse -> save.
        Returns summary dict.
        """
        session: Session = self.session_factory()
        log = SyncLog(started_at=datetime.utcnow())
        session.add(log)
        session.commit()

        try:
            if date_from is None:
                # Check last sync or use lookback
                last = (
                    session.query(Invoice)
                    .order_by(Invoice.eracun_sent.desc())
                    .first()
                )
                if last and last.eracun_sent:
                    date_from = last.eracun_sent - timedelta(days=1)
                else:
                    date_from = datetime.utcnow() - timedelta(days=lookback_days)

            # Query inbox
            logger.info(f"Querying inbox from {date_from} to {date_to or 'now'}")
            items = self.eracun.query_inbox(date_from=date_from, date_to=date_to)
            log.invoices_found = len(items)
            logger.info(f"Found {len(items)} invoices in inbox")

            new_count = 0
            for item in items:
                # Check if already in DB
                existing = (
                    session.query(Invoice)
                    .filter(Invoice.electronic_id == item.electronic_id)
                    .first()
                )
                if existing:
                    # Update eRačun status
                    existing.eracun_status_id = item.status_id
                    existing.eracun_status_name = item.status_name
                    existing.imported = item.imported
                    continue

                # New invoice — download and process
                result = self._process_new_invoice(item, session)
                if result:
                    invoice, ubl_lines = result
                    session.add(invoice)
                    new_count += 1
                    # Ensure supplier mapping row exists (creates unmapped entry if new)
                    supplier_mapping = get_or_create_supplier_mapping(
                        session, invoice.sender_oib, invoice.sender_name
                    )
                    # Auto-register product lines as unmapped entries
                    if ubl_lines:
                        sync_product_mappings_from_lines(session, supplier_mapping, ubl_lines)

            log.invoices_new = new_count
            log.status = "success"
            log.finished_at = datetime.utcnow()
            session.commit()

            result = {
                "found": len(items),
                "new": new_count,
                "status": "success",
            }
            logger.info(f"Sync complete: {result}")
            return result

        except Exception as e:
            log.status = "error"
            log.error_message = str(e)
            log.finished_at = datetime.utcnow()
            session.commit()
            logger.error(f"Sync failed: {e}")
            return {"found": 0, "new": 0, "status": "error", "error": str(e)}
        finally:
            session.close()

    def _process_new_invoice(
        self, item: InboxItem, session: Session
    ) -> Optional[Invoice]:
        """Download, parse, and create Invoice record."""
        try:
            # Create basic record from inbox metadata
            invoice = Invoice(
                electronic_id=item.electronic_id,
                document_nr=item.document_nr,
                document_type_id=item.document_type_id,
                document_type_name=item.document_type_name,
                eracun_status_id=item.status_id,
                eracun_status_name=item.status_name,
                imported=item.imported,
                sender_oib=item.sender_oib,
                sender_name=item.sender_name,
                sender_bu=item.sender_bu,
                eracun_sent=item.sent,
                eracun_delivered=item.delivered,
                eracun_updated=item.updated,
                processing_status="downloaded",
            )

            # Download XML
            ubl = None
            try:
                xml_content = self.eracun.receive(item.electronic_id)
                xml_filename = f"{item.electronic_id}.xml"
                xml_path = self.xml_dir / xml_filename
                xml_path.write_text(xml_content, encoding="utf-8")
                invoice.xml_path = xml_filename

                # Parse UBL
                ubl = parse_ubl_xml(xml_content)
                invoice.invoice_number = ubl.invoice_number or item.document_nr
                invoice.issue_date = ubl.issue_date
                invoice.due_date = ubl.due_date
                invoice.currency_code = ubl.currency_code
                invoice.total_without_vat = ubl.total_without_vat
                invoice.total_vat = ubl.total_vat
                invoice.total_with_vat = ubl.total_with_vat
                invoice.processing_status = "parsed"

                # Extract embedded PDF
                if ubl.embedded_pdf_b64:
                    pdf_filename = f"{item.electronic_id}.pdf"
                    pdf_path = self.pdf_dir / pdf_filename
                    pdf_bytes = base64.b64decode(ubl.embedded_pdf_b64)
                    pdf_path.write_bytes(pdf_bytes)
                    invoice.pdf_path = pdf_filename
                    logger.info(f"Extracted PDF for {item.electronic_id}")

                # Notify eRačun about successful import
                self.eracun.notify_import(item.electronic_id)

            except Exception as e:
                logger.warning(
                    f"Failed to download/parse {item.electronic_id}: {e}"
                )
                invoice.processing_status = "error"
                invoice.processing_error = str(e)
                ubl = None

            return invoice, (ubl.lines if ubl else [])

        except Exception as e:
            logger.error(f"Failed to process {item.electronic_id}: {e}")
            return None

    def import_from_file(self, xml_path: str, session: Session) -> Optional[Invoice]:
        """
        Import an invoice from a local XML file (manual upload).
        Returns Invoice object (not yet committed).
        """
        try:
            xml_content = Path(xml_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            xml_content = Path(xml_path).read_bytes().decode("utf-8", errors="replace")

        ubl = parse_ubl_xml(xml_content)

        # Check for duplicates by invoice number + supplier
        existing = (
            session.query(Invoice)
            .filter(
                Invoice.invoice_number == ubl.invoice_number,
                Invoice.sender_oib == ubl.supplier_oib,
            )
            .first()
        )
        if existing:
            logger.warning(
                f"Duplicate invoice: {ubl.invoice_number} from {ubl.supplier_name}"
            )
            return None

        invoice = Invoice(
            electronic_id=0,  # Manual upload, no eRačun ID
            document_nr=ubl.invoice_number,
            document_type_id=1,
            document_type_name="Račun",
            sender_oib=ubl.supplier_oib,
            sender_name=ubl.supplier_name,
            invoice_number=ubl.invoice_number,
            issue_date=ubl.issue_date,
            due_date=ubl.due_date,
            currency_code=ubl.currency_code,
            total_without_vat=ubl.total_without_vat,
            total_vat=ubl.total_vat,
            total_with_vat=ubl.total_with_vat,
            processing_status="parsed",
        )

        # Save XML
        safe_name = ubl.invoice_number.replace("/", "_").replace("\\", "_")
        xml_filename = f"manual_{safe_name}.xml"
        dest = self.xml_dir / xml_filename
        dest.write_text(xml_content, encoding="utf-8")
        invoice.xml_path = xml_filename

        # Extract PDF if embedded
        if ubl.embedded_pdf_b64:
            pdf_filename = f"manual_{safe_name}.pdf"
            pdf_path = self.pdf_dir / pdf_filename
            pdf_bytes = base64.b64decode(ubl.embedded_pdf_b64)
            pdf_path.write_bytes(pdf_bytes)
            invoice.pdf_path = pdf_filename

        return invoice
