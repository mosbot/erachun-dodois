"""
Dodois authentication via OIDC (auth.dodois.com).
Handles username/password + TOTP 2FA login to obtain officemanager session.
"""
import hashlib
import html
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

    def _extract_hidden(self, html_text: str, name: str) -> Optional[str]:
        """Extract single hidden input value by name (HTML-unescaped)."""
        m = re.search(
            rf'name=["\']?{re.escape(name)}["\']?\s[^>]*value=["\']([^"\']*)["\']',
            html_text
        )
        if not m:
            m = re.search(
                rf'value=["\']([^"\']*)["\'][^>]*name=["\']?{re.escape(name)}["\']?',
                html_text
            )
        return html.unescape(m.group(1)) if m else None

    def _extract_all_hidden(self, html: str) -> dict:
        """Extract all hidden form inputs."""
        fields = {}
        for m in re.finditer(r'<input[^>]+>', html, re.IGNORECASE):
            tag = m.group(0)
            if 'hidden' not in tag.lower():
                continue
            name_m = re.search(r'name=["\']([^"\']+)["\']', tag)
            val_m = re.search(r'value=["\']([^"\']*)["\']', tag)
            if name_m:
                fields[name_m.group(1)] = val_m.group(1) if val_m else ""
        return fields

    def _submit_auto_form(self, session: requests.Session, html: str) -> requests.Response:
        """Submit the JS auto-submit hidden form (OIDC redirect forms)."""
        form_action_m = re.search(r'action=["\']([^"\']+)["\']', html)
        form_action = form_action_m.group(1) if form_action_m else None
        if not form_action:
            raise RuntimeError("No form action found in auto-submit page")
        hidden_fields = self._extract_all_hidden(html)
        logger.info(f"Auto-submitting form to: {form_action}")
        return session.post(form_action, data=hidden_fields, allow_redirects=True, timeout=15)

    def _login(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        })

        logger.info("Logging in to Dodois...")

        # Step 1: Access officemanager — returns auto-submit form to auth.dodois.com/connect/authorize
        r = session.get(OFFICEMANAGER_URL + "/OfficeManager/Supply", allow_redirects=True, timeout=15)
        logger.info(f"Step1 URL: {r.url}")

        if "auth.dodois.com" not in r.text and "connect/authorize" not in r.text:
            logger.info("Already authenticated")
            return session

        # Step 2: Submit OIDC authorize form → lands on /login/password
        r2 = self._submit_auto_form(session, r.text)
        logger.info(f"Step2 URL: {r2.url}")

        # Step 3: POST credentials to /login/password
        # Fields: Login, Password, ReturnUrl, __RequestVerificationToken
        csrf = self._extract_hidden(r2.text, "__RequestVerificationToken")
        return_url = self._extract_hidden(r2.text, "ReturnUrl") or ""
        login_url = r2.url.split("?")[0]
        logger.info(f"Step3 POST to: {login_url}, csrf: {bool(csrf)}")

        payload = {
            "Login": self.username,
            "Password": self.password,
            "ReturnUrl": return_url,
        }
        if csrf:
            payload["__RequestVerificationToken"] = csrf

        r3 = session.post(login_url, data=payload, allow_redirects=True, timeout=15)
        logger.info(f"Step3 URL: {r3.url}")

        # Step 4: 2FA check
        needs_2fa = (
            "loginwith2fa" in r3.url.lower()
            or "twofactor" in r3.url.lower()
            or "two-factor" in r3.url.lower()
            or "TwoFactorCode" in r3.text
            or "/login/2fa" in r3.url
            or "/login/totp" in r3.url
        )
        logger.info(f"Step4 needs 2FA: {needs_2fa}, URL: {r3.url}")

        if needs_2fa:
            html2 = r3.text
            csrf2 = self._extract_hidden(html2, "__RequestVerificationToken")
            # Form action may be relative — resolve to absolute
            tfa_action_m = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', html2, re.I)
            tfa_action = tfa_action_m.group(1) if tfa_action_m else "/mfa/login/totp"
            if tfa_action.startswith("/"):
                tfa_action = AUTH_BASE + tfa_action
            totp_code = self._generate_totp()
            logger.info(f"Submitting TOTP {totp_code} to {tfa_action}")

            # POST to the full URL (preserving returnUrl query param) not just form action
            tfa_post_url = r3.url  # has returnUrl in query string
            payload2 = {"Code": totp_code}
            if csrf2:
                payload2["__RequestVerificationToken"] = csrf2

            r4 = session.post(tfa_post_url, data=payload2, allow_redirects=True, timeout=15)
            logger.info(f"After 2FA: {r4.url}")
            final = r4
        else:
            final = r3

        # Step 5: After 2FA we may be at /profile — re-navigate to officemanager.
        # Session cookies at auth.dodois.com are set, OIDC will complete without re-login.
        for attempt in range(3):
            if "officemanager.dodois.com" in final.url:
                break

            # Handle auto-submit OIDC forms (form_post mode)
            if ("<form" in final.text and "hidden" in final.text and
                    ("connect/authorize" in final.text or "signin-oidc" in final.text or
                     "auth.dodois.com" in final.text)):
                try:
                    final = self._submit_auto_form(session, final.text)
                    logger.info(f"Step5 form-post: {final.url}")
                    continue
                except Exception:
                    pass

            # Re-navigate to officemanager — authenticated session will complete OIDC
            logger.info(f"Step5 re-navigating to officemanager (attempt {attempt+1})")
            final = session.get(OFFICEMANAGER_URL + "/OfficeManager/Supply", allow_redirects=True, timeout=15)
            logger.info(f"Step5 URL: {final.url}")

            # If landed on a new OIDC authorize form, submit it
            if "connect/authorize" in final.text:
                try:
                    final = self._submit_auto_form(session, final.text)
                    logger.info(f"Step5 OIDC re-auth: {final.url}")
                except Exception:
                    break

        if "officemanager.dodois.com" in final.url:
            logger.info("Login successful!")
        else:
            logger.warning(f"Login may have failed, ended at: {final.url}")

        return session

    def _is_oidc_redirect(self, response: requests.Response) -> bool:
        """Return True if the response is an OIDC auto-submit HTML form."""
        if "text/html" not in response.headers.get("content-type", ""):
            return False
        return "auth.dodois.com" in response.text and "<form" in response.text

    def get(self, url: str, **kwargs) -> requests.Response:
        session = self.get_session()
        response = session.get(url, **kwargs)
        if self._is_oidc_redirect(response):
            logger.info("Session expired (OIDC redirect) — re-logging in...")
            self.invalidate()
            session = self.get_session()
            response = session.get(url, **kwargs)
        return response

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.get_session().post(url, **kwargs)

    def invalidate(self):
        """Force re-login on next request."""
        self._session = None
        self._expires_at = None
