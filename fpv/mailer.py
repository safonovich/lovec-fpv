"""Отправка КП по email через SMTP почты коллеги (app password в секретах)."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr


def send(to: str, subject: str, body: str, cfg: dict, log,
         in_reply_to: str | None = None) -> bool:
    e = cfg["email"]
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    if not e.get("enabled") or not user or not pwd:
        log("mailer: SMTP не настроен (секреты SMTP_USER/SMTP_PASS)")
        return False

    msg = EmailMessage()
    msg["From"] = formataddr((cfg["kp"]["author_name"], user))
    msg["To"] = to
    msg["Subject"] = subject
    msg["X-Lovec-FPV"] = "1"             # метка своих писем — inbox их не трогает
    if in_reply_to:                      # чтобы ответ лёг в тот же тред
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    try:
        host, port = e["smtp_host"], int(e.get("smtp_port", 465))
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.starttls()
        with s:
            s.login(user, pwd)
            s.send_message(msg)
        log(f"mailer: отправлено → {to}")
        return True
    except Exception as ex:
        log(f"mailer: ошибка отправки → {to} — {ex}")
        return False
