"""
Transactional email.

Two transports, chosen by which credentials are present:

* **HTTP API (preferred).** Render's free tier blocks outbound traffic on
  every SMTP port (25, 465, 587), and port 25 is blocked on all plans, so an
  SMTP mailer works locally and then fails in production. An HTTPS request to
  the provider's API goes out over 443 like any other call and is unaffected.
* **SMTP.** Kept for local development and for hosts that allow it.

Delivery is synchronous: a reset code is worthless if it arrives after the
user has given up, and the caller needs to know whether it actually went.
"""
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from dotenv import load_dotenv

# Settings are read at CALL time, not import time. Reading them into module
# constants meant the value depended on whether something else had already
# loaded the .env file — the mailer reported "not configured" with a valid
# key sitting right there.
load_dotenv()

# Brevo's REST API. Free tier is 300 emails/day with no expiry, and the
# sending address can be a plain verified mailbox — no domain required.
BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def _brevo_api_key() -> str:
    return os.getenv("BREVO_API_KEY", "")


def _smtp_host() -> str:
    return os.getenv("SMTP_HOST", "")


def _smtp_user() -> str:
    return os.getenv("SMTP_USER", "")


def _smtp_password() -> str:
    return os.getenv("SMTP_PASSWORD", "")


def _smtp_port() -> int:
    return int(os.getenv("SMTP_PORT", "587"))


def _mail_from() -> str:
    return os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM") or _smtp_user()


def _mail_from_name() -> str:
    return os.getenv("MAIL_FROM_NAME") or os.getenv("SMTP_FROM_NAME") or "Billing"


def _timeout() -> int:
    return int(os.getenv("MAIL_TIMEOUT", "20"))


class MailNotConfigured(RuntimeError):
    """No transport is configured, so nothing can be sent."""


class MailSendFailed(RuntimeError):
    """The provider refused the message. Carries the provider's own reason."""


def _smtp_configured() -> bool:
    return bool(_smtp_host() and _smtp_user() and _smtp_password() and _mail_from())


def is_configured() -> bool:
    return bool(_brevo_api_key() and _mail_from()) or _smtp_configured()


def transport_name() -> str:
    """Which transport a send would use — surfaced in logs and /health."""
    if _brevo_api_key() and _mail_from():
        return "brevo-api"
    if _smtp_configured():
        return "smtp"
    return "none"


def _send_via_brevo(to: str, subject: str, text_body: str, html_body: str) -> None:
    payload = {
        "sender": {"email": _mail_from(), "name": _mail_from_name()},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text_body,
    }
    if html_body:
        payload["htmlContent"] = html_body

    request = urllib.request.Request(
        BREVO_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "api-key": _brevo_api_key(),
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_timeout()) as response:
            if response.status not in (200, 201, 202):
                raise MailSendFailed(f"Brevo returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        # Brevo explains refusals in the body (unverified sender, bad key,
        # quota) — losing that turns every failure into "it didn't work".
        detail = exc.read().decode(errors="replace")[:300]
        raise MailSendFailed(f"Brevo rejected the message ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise MailSendFailed(f"Could not reach Brevo: {exc.reason}") from exc


def _send_via_smtp(to: str, subject: str, text_body: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{_mail_from_name()} <{_mail_from()}>"
    message["To"] = to
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    try:
        # 465 is implicit TLS; 587 and 25 start in the clear and upgrade.
        if _smtp_port() == 465:
            with smtplib.SMTP_SSL(
                _smtp_host(), _smtp_port(), timeout=_timeout(), context=context
            ) as server:
                server.login(_smtp_user(), _smtp_password())
                server.send_message(message)
            return

        with smtplib.SMTP(_smtp_host(), _smtp_port(), timeout=_timeout()) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(_smtp_user(), _smtp_password())
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailSendFailed(f"SMTP delivery failed: {exc}") from exc


def send_email(to: str, subject: str, text_body: str, html_body: str = "") -> None:
    """Send one message over whichever transport is configured."""
    transport = transport_name()

    if transport == "brevo-api":
        _send_via_brevo(to, subject, text_body, html_body)
    elif transport == "smtp":
        _send_via_smtp(to, subject, text_body, html_body)
    else:
        raise MailNotConfigured(
            "No email transport configured — set BREVO_API_KEY and MAIL_FROM "
            "(recommended), or SMTP_HOST/SMTP_USER/SMTP_PASSWORD/MAIL_FROM."
        )


def send_password_reset_code(to: str, code: str, minutes_valid: int) -> None:
    """The reset email itself — one code, and nothing to click."""
    subject = f"{code} is your Billing password reset code"
    text_body = (
        f"Your Billing password reset code is {code}.\n\n"
        f"It expires in {minutes_valid} minutes and can be used once.\n\n"
        "If you did not ask to reset your password, you can ignore this "
        "email — your password has not changed."
    )
    html_body = f"""\
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
 color:#0F172A;line-height:1.5">
  <p>Your Billing password reset code is:</p>
  <p style="font-size:30px;font-weight:700;letter-spacing:5px;color:#2563EB;
     margin:20px 0">{code}</p>
  <p>It expires in {minutes_valid} minutes and can be used once.</p>
  <p style="color:#64748B;font-size:13px">If you did not ask to reset your
     password, you can ignore this email — your password has not changed.</p>
</body></html>"""

    send_email(to, subject, text_body, html_body)
