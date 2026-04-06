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
        r.raise_for_status()
        return r.json()
