"""
Dodois REST API client for creating and reading supplies.
Uses DodoisSession for cookie-based authentication.
"""
import logging
from typing import Optional
from app.core.dodois_auth import DodoisSession

logger = logging.getLogger(__name__)

BASE = "https://officemanager.dodois.com/Accounting/v1"
DEPT_ID = "E67B8C27D336AE8311EDE29371DEF8F6"


class DodoisClient:
    def __init__(self, session: DodoisSession):
        self._session = session

    def get_suppliers(self) -> list:
        r = self._session.get(f"{BASE}/Suppliers?departmentId={DEPT_ID}", timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def get_raw_materials(self, supplier_id: str) -> list:
        r = self._session.get(
            f"{BASE}/incomingstock/departments/{DEPT_ID}/suppliers/{supplier_id}/rawmaterials",
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def get_all_supplies(self, dept_id: str, from_date: str, to_date: str) -> list:
        """Fetch all supplies for a department in date range (handles pagination)."""
        page, page_size = 1, 100
        all_supplies = []
        while True:
            r = self._session.get(
                f"{BASE}/incomingstock/departments/{dept_id}/supplies"
                f"?from={from_date}&to={to_date}"
                f"&pagination.current={page}&pagination.pageSize={page_size}",
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            # API returns {"supplies": [...], "pagination": {...}}
            if isinstance(data, dict):
                items = data.get("supplies") or data.get("items") or []
                pag = data.get("pagination") or {}
                total = pag.get("total") or data.get("total") or 0
            else:
                items = data
                pag = {}
                total = 0
            if not items:
                break
            all_supplies.extend(items)
            total = total or len(items)
            if len(all_supplies) >= total or len(items) < page_size:
                break
            page += 1
        return all_supplies

    def get_supply_detail(self, supply_id: str) -> dict:
        """Fetch a single supply with its line items."""
        r = self._session.get(
            f"{BASE}/incomingstock/supplies/{supply_id}",
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_supplies(self, page: int = 1, page_size: int = 50) -> dict:
        r = self._session.get(
            f"{BASE}/incomingstock/departments/{DEPT_ID}/supplies"
            f"?pagination.current={page}&pagination.pageSize={page_size}",
            timeout=15
        )
        r.raise_for_status()
        return r.json()

    def create_supply(self, payload: dict) -> dict:
        r = self._session.post(
            f"{BASE}/incomingstock/supplies",
            json=payload,
            timeout=30
        )
        if r.status_code >= 400:
            body = (r.text or "")[:2000]
            logger.error(
                "Dodois create_supply failed: %s %s\nPayload: %s\nResponse: %s",
                r.status_code, r.reason,
                __import__("json").dumps(payload, ensure_ascii=False),
                body,
            )
            raise RuntimeError(
                f"Dodois {r.status_code} {r.reason}: {body}"
            )
        return r.json()
