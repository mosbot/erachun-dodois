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
# Hard cap on pagination. Verified: a server that ignores paging.offset was
# still issuing requests after 501 iterations, which hangs list_operations
# inside the flock in sync_invoices.sh -- every later cron tick then prints
# "another sync in progress, skipping" and exits 0, silently stopping
# eRačun ingestion too. Once the cap is hit we raise rather than return a
# partial list: a partial list looks exactly like "no existing operation"
# to the caller and would risk posting a duplicate.
MAX_PAGES = 50


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

    def _request_with_retries(self, method: str, url: str, **kwargs):
        """Execute HTTP request with retry logic for transient failures.

        Retries on:
        - requests.Timeout, requests.ConnectionError (network errors)
        - 5xx status codes (server errors)

        Does NOT retry on:
        - 4xx status codes (client errors)

        Raises PlanfactError on all failure paths after exhausting retries.
        Returns the requests.Response object on success.
        """
        last_error = "no attempt made"

        for attempt in range(1, self.max_retries + 1):
            try:
                r = getattr(self.session, method)(url, timeout=self.timeout,
                                                   **kwargs)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("PlanFact network error (attempt %d/%d): %s",
                               attempt, self.max_retries, exc)
            else:
                if r.status_code < 500:
                    # 2xx, 3xx, 4xx all return immediately (no retry).
                    return r
                # 5xx: retry
                last_error = f"HTTP {r.status_code}: {(r.text or '')[:300]}"
                logger.warning("PlanFact server error (attempt %d/%d): %s",
                               attempt, self.max_retries, last_error)

            if attempt < self.max_retries:
                time.sleep(self.retry_delay * attempt)

        raise PlanfactError(
            f"giving up after {self.max_retries} attempts: {last_error}")

    def create_outcome(self, payload: dict) -> dict:
        url = f"{self.base_url}/operations/outcome"
        r = self._request_with_retries("post", url, json=payload)

        if 200 <= r.status_code < 300:
            # A successful POST may answer with no body at all.
            if not (r.text or "").strip():
                return {}
            data = r.json()
            if data.get("isSuccess") is False:
                raise PlanfactError(
                    f"{data.get('errorMessage', 'unknown error')} "
                    f"(code={data.get('errorCode')})")
            return data
        # 4xx client error
        raise PlanfactError(
            f"HTTP {r.status_code}: {(r.text or '')[:300]}")

    def list_operations(self, account_id: int,
                        date_from: str, date_to: str) -> list:
        """Return every operation on an account within an inclusive date range.

        Raises PlanfactError if MAX_PAGES is exhausted instead of returning
        whatever was collected so far — see MAX_PAGES for why a partial
        list is worse than an explicit failure here.
        """
        url = f"{self.base_url}/operations/list"
        out, offset = [], 0
        for _ in range(MAX_PAGES):
            r = self._request_with_retries(
                "post",
                url,
                params={"paging.offset": offset, "paging.limit": PAGE_SIZE},
                json={"accountId": [account_id],
                      "operationDateStart": date_from,
                      "operationDateEnd": date_to},
            )
            if not (200 <= r.status_code < 300):
                raise PlanfactError(
                    f"list_operations HTTP {r.status_code}: {(r.text or '')[:300]}")
            items = ((r.json().get("data") or {}).get("items") or [])
            if not items:
                return out
            out.extend(items)
            if len(items) < PAGE_SIZE:
                return out
            offset += PAGE_SIZE

        raise PlanfactError(
            f"list_operations exceeded MAX_PAGES={MAX_PAGES} for account "
            f"{account_id} ({date_from}..{date_to}) without reaching a short "
            f"or empty page — aborting rather than returning a partial list, "
            f"which would look like 'no existing operation' to the caller "
            f"and risk posting a duplicate")
