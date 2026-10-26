"""邮件推送（可选）。所需环境变量：
SMTP_HOST, SMTP_PORT(默认465), SMTP_USER, SMTP_PASSWORD, MAIL_TO(多个用逗号分隔), MAIL_FROM(可选)
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


def email_configured() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"))


def send_email(subject: str, markdown_text: str, html: str):
    host, user = os.environ["SMTP_HOST"], os.environ["SMTP_USER"]
    port = int(os.getenv("SMTP_PORT") or "465")
    to = [x.strip() for x in os.environ["MAIL_TO"].split(",") if x.strip()]
    sender = os.getenv("MAIL_FROM", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(markdown_text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
        server.starttls()
    with server:
        server.login(user, os.environ["SMTP_PASSWORD"])
        server.sendmail(sender, to, msg.as_string())
    log.info("邮件已发送至 %s", ", ".join(to))
