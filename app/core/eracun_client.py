"""
moj-eRačun API Client v2
API docs: https://manual.moj-eracun.hr/documentation/api-specification/
"""

import httpx
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EracunCredentials:
    username: str
    password: str
    company_id: str
    software_id: str
    company_bu: str = ""


@dataclass
class InboxItem:
    """A single item from QueryInbox response."""
    electronic_id: int
    document_nr: str
    document_type_id: int
    document_type_name: str
    status_id: int
    status_name: str
    sender_oib: str
    sender_bu: str
    sender_name: str
    updated: Optional[datetime] = None
    sent: Optional[datetime] = None
    delivered: Optional[datetime] = None
    imported: bool = False


class EracunClient:
    """Client for moj-eRačun REST API v2."""

    def __init__(self, base_url: str, credentials: EracunCredentials):
        self.base_url = base_url.rstrip("/")
        self.creds = credentials
        self.client = httpx.Client(timeout=30.0)

    def _base_payload(self) -> dict:
        """Common auth fields for every request."""
        return {
            "Username": self.creds.username,
            "Password": self.creds.password,
            "CompanyId": self.creds.company_id,
            "CompanyBu": self.creds.company_bu,
            "SoftwareId": self.creds.software_id,
        }

    def _post(self, endpoint: str, extra: dict = None) -> dict:
        """POST to API with auth + extra fields."""
        payload = self._base_payload()
        if extra:
            payload.update(extra)
        url = f"{self.base_url}/{endpoint}"
        logger.info(f"POST {url}")
        resp = self.client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        resp.raise_for_status()
        return resp.json()

    def ping(self) -> bool:
        """Check if API is up."""
        try:
            resp = self._post("ping")
            return True
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False

    def query_inbox(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status_id: Optional[int] = None,
        electronic_id: Optional[int] = None,
    ) -> list[InboxItem]:
        """
        Get list of incoming invoices.

        Status codes:
            20 - In Validation
            30 - Sent (waiting for download)
            40 - Delivered / Processed
            50 - Rejected
            60 - Expired
        """
        extra = {}
        if date_from:
            extra["From"] = date_from.strftime("%Y-%m-%dT%H:%M:%S")
        if date_to:
            extra["To"] = date_to.strftime("%Y-%m-%dT%H:%M:%S")
        if status_id is not None:
            extra["StatusId"] = str(status_id)
        if electronic_id is not None:
            extra["ElectronicId"] = electronic_id

        data = self._post("queryInbox", extra)

        # Response is a list of items
        items = []
        if isinstance(data, list):
            for d in data:
                items.append(self._parse_inbox_item(d))
        elif isinstance(data, dict) and "ElectronicId" in data:
            # Single item response
            items.append(self._parse_inbox_item(data))

        return items

    def receive(self, electronic_id: int) -> str:
        """
        Download a single invoice XML by ElectronicID.
        Returns the XML content as string.
        """
        extra = {"ElectronicId": electronic_id}
        resp = self.client.post(
            f"{self.base_url}/receive",
            json={**self._base_payload(), **extra},
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        resp.raise_for_status()

        # Response contains the XML document
        data = resp.json()
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            # XML might be in a field
            return data.get("Document", data.get("Xml", str(data)))
        return str(data)

    def notify_import(self, electronic_id: int) -> bool:
        """
        Notify MER that document was successfully imported.
        Prevents duplicate imports (sets Imported=true).
        Optional but recommended.
        """
        try:
            payload = self._base_payload()
            resp = self.client.post(
                f"{self.base_url}/notifyimport/{electronic_id}",
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            resp.raise_for_status()
            logger.info(f"NotifyImport OK for {electronic_id}")
            return True
        except Exception as e:
            logger.error(f"NotifyImport failed for {electronic_id}: {e}")
            return False

    def update_process_status(self, electronic_id: int, status: int) -> bool:
        """
        Update document process status.

        Status codes:
            0 - APPROVED (Prihvaćen)
            1 - REJECTED (Odbijen)
            2 - PAYMENT_FULFILLED (Plaćeno u potpunosti)
            3 - PAYMENT_PARTIALLY_FULFILLED (Djelomično plaćeno)
        """
        try:
            payload = self._base_payload()
            payload["ElectronicId"] = electronic_id
            payload["DokumentProcessStatus"] = status
            resp = self.client.post(
                f"{self.base_url}/UpdateDokumentProcessStatus",
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            resp.raise_for_status()
            logger.info(f"UpdateProcessStatus OK: {electronic_id} -> {status}")
            return True
        except Exception as e:
            logger.error(f"UpdateProcessStatus failed: {e}")
            return False

    def _parse_inbox_item(self, d: dict) -> InboxItem:
        """Parse a single inbox item from API response."""
        return InboxItem(
            electronic_id=d.get("ElectronicId", 0),
            document_nr=d.get("DocumentNr", ""),
            document_type_id=d.get("DocumentTypeId", 0),
            document_type_name=d.get("DocumentTypeName", ""),
            status_id=d.get("StatusId", 0),
            status_name=d.get("StatusName", ""),
            sender_oib=d.get("SenderBusinessNumber", ""),
            sender_bu=d.get("SenderBusinessUnit", ""),
            sender_name=d.get("SenderBusinessName", ""),
            updated=self._parse_dt(d.get("Updated")),
            sent=self._parse_dt(d.get("Sent")),
            delivered=self._parse_dt(d.get("Delivered")),
            imported=d.get("Imported", False),
        )

    @staticmethod
    def _parse_dt(val: Optional[str]) -> Optional[datetime]:
        if not val:
            return None
        try:
            # Handle ISO format with optional microseconds
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
