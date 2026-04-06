"""
Dodois authentication via OIDC (auth.dodois.com).
Handles username/password + TOTP 2FA login to obtain officemanager session.
"""
import hashlib
import logging
import re
import pyotp
import requests
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

OFFICEMANAGER_URL = "https://officemanager.dodois.com"
AUTH_BASE = "https://auth.dodois.com"


class DodoisSession:
    """Manages authenticated session to officemanager.dodois.com."""

    def __init__(self, username: str, password: str, totp_secret: str):
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self._session: Optional[requests.Session] = None
        self._expires_at: Optional[datetime] = None

    def get_session(self) -> requests.Session:
        """Return authenticated session, re-login if expired."""
        if self._session is None or self._is_expired():
            self._session = self._login()
            self._expires_at = datetime.utcnow() + timedelta(hours=8)
        return self._session

    def _is_expired(self) -> bool:
        if self._expires_at is None:
            return True
        return datetime.utcnow() >= self._expires_at

    def _generate_totp(self) -> str:
        totp = pyotp.TOTP(self.totp_secret, digest=hashlib.sha256)
        return totp.now()

    def _extract_hidden(self, html: str, name: str) -> Optional[str]:
        m = re.search(rf'name=["\']?{re.escape(name)}["\']?\s+value=["\']([^"\']+)["\']', html)
        if not m:
            m = re.search(rf'value=["\']([^"\']+)["\']\s+name=["\']?{re.escape(name)}["\']?', html)
        return m.group(1) if m else None

    def _login(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; erachun-dodois/1.0)',
        })

        logger.info("Logging in to Dodois...")

        # Step 1: Access officemanager → redirect to auth login
        r = session.get(OFFICEMANAGER_URL + "/OfficeManager/Supply", allow_redirects=True, timeout=15)
        if "officemanager.dodois.com" in r.url and "authenticate" not in r.url.lower() and "login" not in r.url.lower():
            logger.info("Already authenticated")
            return session

        # Step 2: Parse login form
        html = r.text
        token = self._extract_hidden(html, "__RequestVerificationToken")
        return_url = re.search(r'[?&]ReturnUrl=([^&"\']+)', r.url)
        return_url_val = return_url.group(1) if return_url else ""

        # Also try to find returnUrl in form
        if not return_url_val:
            return_url_val = self._extract_hidden(html, "ReturnUrl") or ""

        login_url = f"{AUTH_BASE}/account/login"
        if "auth.dodois.com" in r.url:
            login_url = r.url.split("?")[0]

        # Step 3: POST credentials
        payload = {
            "Input.Username": self.username,
            "Input.Password": self.password,
            "Input.RememberLogin": "true",
            "button": "login",
        }
        if token:
            payload["__RequestVerificationToken"] = token
        if return_url_val:
            payload["Input.ReturnUrl"] = return_url_val

        r2 = session.post(login_url, data=payload, allow_redirects=True, timeout=15)

        # Step 4: Check for 2FA page
        if "loginwith2fa" in r2.url or "TwoFactor" in r2.text or "two" in r2.url.lower():
            html2 = r2.text
            token2 = self._extract_hidden(html2, "__RequestVerificationToken")
            return_url2 = self._extract_hidden(html2, "ReturnUrl") or return_url_val

            totp_code = self._generate_totp()
            logger.info(f"Submitting TOTP code: {totp_code}")

            tfa_url = r2.url.split("?")[0]
            payload2 = {
                "Input.TwoFactorCode": totp_code,
                "Input.RememberMachine": "true",
                "button": "login",
            }
            if token2:
                payload2["__RequestVerificationToken"] = token2
            if return_url2:
                payload2["Input.ReturnUrl"] = return_url2

            r3 = session.post(tfa_url, data=payload2, allow_redirects=True, timeout=15)

            if "officemanager.dodois.com" not in r3.url:
                # Try alternative field name
                payload2["TwoFactorCode"] = payload2.pop("Input.TwoFactorCode")
                r3 = session.post(tfa_url, data=payload2, allow_redirects=True, timeout=15)

            logger.info(f"After 2FA: {r3.url}")
        else:
            r3 = r2

        if "officemanager.dodois.com" in r3.url:
            logger.info("Login successful")
        else:
            logger.warning(f"Login may have failed, ended at: {r3.url}")

        return session

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.get_session().get(url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.get_session().post(url, **kwargs)

    def invalidate(self):
        """Force re-login on next request."""
        self._session = None
        self._expires_at = None
