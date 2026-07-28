"""
PlanFact API client.

Base URL: https://api.planfact.io/api/v1
Auth:     X-ApiKey header
Docs:     https://apidoc.planfact.io/

Only two calls are needed: create an outcome operation, and list operations so
we can tell whether one already exists. PlanFact does NOT enforce externalId
uniqueness — five duplicated operations already exist from the previous
service — so listing is a required part of posting, not an optimisation.
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


class PlanfactError(RuntimeError):
    """PlanFact rejected the request, or kept failing after retries."""


class PlanfactClient:
    def __init__(self, api_key: str,
                 base_url: str = "https://api.planfact.io/api/v1",
                 timeout: float = 15.0,
                 max_retries: int = 3,
                 retry_delay: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self.session.headers.update({
            "X-ApiKey": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def create_outcome(self, payload: dict) -> dict:
        url = f"{self.base_url}/operations/outcome"
        last_error = "no attempt made"

        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.post(url, json=payload, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("PlanFact network error (attempt %d/%d): %s",
                               attempt, self.max_retries, exc)
            else:
                if r.status_code in (200, 201):
                    # A successful POST may answer with no body at all.
                    if not (r.text or "").strip():
                        return {}
                    data = r.json()
                    if data.get("isSuccess") is False:
                        raise PlanfactError(
                            f"{data.get('errorMessage', 'unknown error')} "
                            f"(code={data.get('errorCode')})")
                    return data
                if r.status_code < 500:
                    raise PlanfactError(
                        f"HTTP {r.status_code}: {(r.text or '')[:300]}")
                last_error = f"HTTP {r.status_code}: {(r.text or '')[:300]}"
                logger.warning("PlanFact server error (attempt %d/%d): %s",
                               attempt, self.max_retries, last_error)

            if attempt < self.max_retries:
                time.sleep(self.retry_delay * attempt)

        raise PlanfactError(
            f"giving up after {self.max_retries} attempts: {last_error}")

    def list_operations(self, account_id: int,
                        date_from: str, date_to: str) -> list:
        """Return every operation on an account within an inclusive date range."""
        url = f"{self.base_url}/operations/list"
        out, offset = [], 0
        while True:
            r = self.session.post(
                url,
                params={"paging.offset": offset, "paging.limit": PAGE_SIZE},
                json={"accountId": [account_id],
                      "operationDateStart": date_from,
                      "operationDateEnd": date_to},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                raise PlanfactError(
                    f"list_operations HTTP {r.status_code}: {(r.text or '')[:300]}")
            items = ((r.json().get("data") or {}).get("items") or [])
            out.extend(items)
            if len(items) < PAGE_SIZE:
                return out
            offset += PAGE_SIZE
