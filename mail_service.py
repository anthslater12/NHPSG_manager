"""Outbound email support for NHPSG Manager."""

import os
import smtplib
from email.message import EmailMessage


def _mail_configuration():
    """Return validated outbound-mail configuration from environment variables."""
    required = {
        "server": os.environ.get("NHPSG_MAIL_SERVER", "").strip(),
        "port": os.environ.get("NHPSG_MAIL_PORT", "").strip(),
        "username": os.environ.get("NHPSG_MAIL_USERNAME", "").strip(),
        "password": os.environ.get("NHPSG_MAIL_PASSWORD", ""),
        "from_address": os.environ.get("NHPSG_MAIL_FROM", "").strip(),
    }

    missing = [
        name
        for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "NHPSG mail configuration is incomplete: "
            + ", ".join(missing)
        )

    try:
        port = int(required["port"])
    except ValueError as error:
        raise RuntimeError(
            "NHPSG_MAIL_PORT must be a valid integer."
        ) from error

    return {
        "server": required["server"],
        "port": port,
        "username": required["username"],
        "password": required["password"],
        "from_address": required["from_address"],
    }


def send_email(to_address, subject, body):
    """Send one plain-text email through the configured SMTP service."""
    recipient = (to_address or "").strip()
    subject = (subject or "").strip()

    if not recipient:
        raise ValueError("A recipient email address is required.")

    if not subject:
        raise ValueError("An email subject is required.")

    config = _mail_configuration()

    message = EmailMessage()
    message["From"] = config["from_address"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body or "")

    with smtplib.SMTP(
        config["server"],
        config["port"],
        timeout=30,
    ) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(
            config["username"],
            config["password"],
        )
        smtp.send_message(message)
