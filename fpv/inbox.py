"""Входящие: проверяем ящик по IMAP, находим ответы агентств из базы,
LLM готовит черновик ответа — отправка ТОЛЬКО по кнопке в Telegram.
Сложные случаи (договор, юр. вопросы, негатив) — handoff: бот зовёт человека."""

from __future__ import annotations

import email
import email.utils
import imaplib
import json
import os
import time

from fpv import llm, notify

PUBLIC_DOMAINS = {"mail.ru", "gmail.com", "yandex.ru", "ya.ru", "bk.ru",
                  "inbox.ru", "list.ru", "rambler.ru", "icloud.com",
                  "outlook.com", "hotmail.com"}

SYSTEM = """Ты — ассистент FPV-пилота ({author}), который разослал агентствам
недвижимости КП о съёмке объектов FPV-дроном. Агентство ответило на письмо.

Напиши короткий деловой ответ (60–120 слов). Цель — довести до конкретики:
получить адрес объекта, назначить созвон или встречу.

Жёсткие правила:
- цены ТОЛЬКО из прайса, новые не выдумывай:
{prices}
- оплата: {payment}
- скидки не предлагай, даты съёмок не обещай («согласуем дату — под ваш объект найдём слот»)
- криптовалюту не упоминай
- не выдумывай факты, которых нет в переписке
- подпись: {author}, {phone}

Если в письме: вопросы по договору/юридические, претензия, негатив, просьба
о нестандартных условиях (в т.ч. крипта) — НЕ отвечай сам, верни handoff.

Ответь СТРОГО одним JSON-объектом:
{{"handoff": false, "body": "текст ответа"}}
или
{{"handoff": true, "reason": "почему нужен человек, до 15 слов"}}"""


def _body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def _strip_quotes(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        if ln.strip().startswith(">"):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()[:1500]


def _match(addr: str, agencies: list[dict]):
    addr = addr.lower().strip()
    if not addr or "@" not in addr:
        return None
    dom = addr.split("@")[-1]
    active = [a for a in agencies
              if a.get("email") and a.get("status") in ("sent", "replied")]
    for a in active:
        if a["email"].lower() == addr:
            return a
    if dom in PUBLIC_DOMAINS:
        return None
    for a in active:
        if a["email"].lower().split("@")[-1] == dom:
            return a
    return None


def _draft(agency: dict, incoming: str, cfg: dict, log):
    """Возвращает {"handoff":..., "body"/"reason":...} или None (нет ключа/ошибка)."""
    kp_cfg = cfg["kp"]
    system = SYSTEM.format(author=kp_cfg["author_name"],
                           phone=kp_cfg["author_phone"],
                           prices=kp_cfg.get("prices", ""),
                           payment=kp_cfg.get("payment", ""))
    user = (f"Агентство: {agency['name']}\n"
            f"Их письмо:\n{incoming}")
    txt = llm.chat(system, user, cfg, log, max_tokens=600)
    if not txt:
        return None
    try:
        return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception as e:
        log(f"inbox: не разобрал ответ LLM ({e})")
        return None


def check(agencies: list[dict], pending: dict, last_uid: int, cfg: dict, log) -> int:
    """Проверяет ящик, возвращает новый last_uid. agencies/pending правятся на месте."""
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    host = cfg["email"].get("imap_host", "").strip()
    if not (user and pwd and host):
        return last_uid

    try:
        M = imaplib.IMAP4_SSL(host, timeout=30)
        M.login(user, pwd)
        M.select("INBOX")
        _, data = M.uid("search", None, f"UID {last_uid + 1}:*")
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid]
    except Exception as e:
        log(f"inbox: IMAP не сработал — {e}")
        return last_uid

    new_last = last_uid
    for uid in uids:
        new_last = max(new_last, uid)
        try:
            _, md = M.uid("fetch", str(uid), "(BODY.PEEK[])")
            msg = email.message_from_bytes(md[0][1])
        except Exception:
            continue
        if msg.get("X-Lovec-FPV"):       # собственное письмо бота — пропускаем
            continue
        from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
        agency = _match(from_addr, agencies)
        if not agency:
            continue

        incoming = _strip_quotes(_body_text(msg))
        subj = msg.get("Subject", "")
        agency["status"] = "replied"
        log(f"inbox: ответ от {agency['name']} ({from_addr})")

        draft = _draft(agency, incoming, cfg, log)
        sk = notify.short_key(f"reply:{uid}:{agency['id']}")
        if draft and not draft.get("handoff") and draft.get("body"):
            pending[sk] = {"kind": "reply", "agency_id": agency["id"],
                           "to": from_addr,
                           "subject": ("Re: " + subj.replace("Re: ", "").strip())[:150],
                           "body": str(draft["body"]),
                           "msgid": msg.get("Message-ID"), "ts": time.time()}
            notify.send_reply_card(agency, incoming, str(draft["body"]), sk, log)
        else:
            reason = (draft or {}).get("reason", "LLM недоступен")
            notify.send_service(
                f"📨 Ответ от {agency['name']} ({from_addr}) — нужен твой ответ "
                f"({reason}).\n\n«{incoming[:600]}»", log)

    try:
        M.logout()
    except Exception:
        pass
    return new_last
