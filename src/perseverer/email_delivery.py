"""One shared outbound SMTP relay for the whole deployment -- the transport under the
weekly/monthly training-report emails (see `email_reports.py`). Deliberately not a per-athlete
credential: it's one mail server for the deployment, configured via `PERSEVERER_SMTP_*` env
vars (`config.py`), exactly like `PERSEVERER_STALENESS_WEBHOOK_URL` or the backup host -- the
per-athlete part is only the opt-in toggle (`athlete_email_report_config`) and the recipient
address (`athlete.email`).

stdlib `smtplib` + `email.message.EmailMessage` only, no dependency. `smtp_security` picks the
wire mode: "starttls" (port 587, mail submission -- connect plaintext then upgrade in place),
"ssl" (port 465, implicit TLS from the first byte), or "none" (a local unauthenticated relay --
no TLS, no login).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from perseverer.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


def send_email(
    settings: Settings,
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    """Send one multipart/alternative message. Raises (never swallows) on any SMTP/connection
    failure -- callers decide: the scheduled jobs log-and-continue per athlete, the
    `POST /settings/email-reports/test` endpoint turns it into a 502. Assumes
    `settings.smtp_configured` was already checked by the caller when `smtp_security != "none"`.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or (settings.smtp_username or "perseverer@localhost")
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    host = settings.smtp_host or "localhost"
    port = settings.smtp_port

    if settings.smtp_security == "ssl":
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_S, context=context) as client:
            _login_and_send(client, settings, msg)
    else:
        with smtplib.SMTP(host, port, timeout=_TIMEOUT_S) as client:
            if settings.smtp_security == "starttls":
                client.starttls(context=ssl.create_default_context())
            _login_and_send(client, settings, msg)

    logger.info("sent email to %s: %s", to, subject)


def _login_and_send(client: smtplib.SMTP, settings: Settings, msg: EmailMessage) -> None:
    # "none" is a local relay that neither wants nor accepts AUTH -- skip login there.
    if settings.smtp_security != "none" and settings.smtp_username and settings.smtp_password:
        client.login(settings.smtp_username, settings.smtp_password)
    client.send_message(msg)
