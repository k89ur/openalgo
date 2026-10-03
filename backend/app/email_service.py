from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Email delivery is not configured: set {name}.")
    return value


def _send(to_email: str, subject: str, text_body: str) -> None:
    host = _required("PIPSGOX_SMTP_HOST")
    username = os.getenv("PIPSGOX_SMTP_USERNAME", "").strip()
    password = os.getenv("PIPSGOX_SMTP_PASSWORD", "")
    from_email = _required("PIPSGOX_EMAIL_FROM")
    port = int(os.getenv("PIPSGOX_SMTP_PORT", "587"))
    security = os.getenv("PIPSGOX_SMTP_SECURITY", "starttls").strip().lower()

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)

    context = ssl.create_default_context()

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    elif security == "none":
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)


def send_verification_email(*, email: str, token: str) -> None:
    web_url = os.getenv("PIPSGOX_WEB_URL", "http://localhost:3001").rstrip("/")
    link = f"{web_url}/api/auth/email/verify?token={quote(token, safe='')}"
    _send(
        email,
        "Verify your PIPSGOX email address",
        (
            "Verify your PIPSGOX email address by opening this link:\n\n"
            f"{link}\n\n"
            "This link expires in 24 hours and can only be used once.\n"
            "If you did not request this, you can ignore this email."
        ),
    )
