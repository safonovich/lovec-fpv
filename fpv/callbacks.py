"""Кнопки: забираем нажатия через getUpdates и выполняем действия.
s|<sk> — отправить КП на email агентства, x|<sk> — пропустить.
У бота НЕ должен стоять webhook (боты из BotFather по умолчанию без него)."""

from __future__ import annotations

import os
import time

import requests

from fpv import mailer


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/{method}"


def _answer(cq_id: str, text: str) -> None:
    try:
        requests.post(_api("answerCallbackQuery"),
                      json={"callback_query_id": cq_id, "text": text}, timeout=10)
    except Exception:
        pass


def _mark(cq: dict, label: str) -> None:
    try:
        msg = cq.get("message") or {}
        requests.post(_api("editMessageReplyMarkup"), json={
            "chat_id": msg.get("chat", {}).get("id"),
            "message_id": msg.get("message_id"),
            "reply_markup": {"inline_keyboard":
                             [[{"text": label, "callback_data": "noop|x"}]]},
        }, timeout=10)
    except Exception:
        pass


def process(pending: dict, agencies: list[dict], offset: int, cfg: dict, log) -> int:
    """Возвращает новый offset. pending и agencies правятся на месте."""
    try:
        r = requests.get(_api("getUpdates"),
                         params={"offset": offset, "timeout": 0}, timeout=25)
        data = r.json()
        if not data.get("ok"):
            log(f"callbacks: Telegram отверг getUpdates — {data.get('description')}"
                " (если тут ошибка 409/webhook — у бота настроен webhook,"
                " нужен отдельный бот без webhook)")
            return offset
        updates = data.get("result", [])
    except Exception as e:
        log(f"callbacks: getUpdates не сработал — {e}")
        return offset

    by_id = {a["id"]: a for a in agencies}
    new_offset = offset
    for u in updates:
        new_offset = max(new_offset, u["update_id"] + 1)
        cq = u.get("callback_query")
        if not cq or "|" not in cq.get("data", ""):
            continue
        action, sk = cq["data"].split("|", 1)
        info = pending.get(sk)
        if action == "noop" or not info:
            _answer(cq["id"], "Карточка устарела" if action != "noop" else "")
            continue
        agency = by_id.get(info["agency_id"])
        if not agency:
            _answer(cq["id"], "Агентство не найдено в базе")
            continue

        if action == "r":               # отправить ответ агентству (переговоры)
            if info.get("kind") != "reply":
                _answer(cq["id"], "Карточка устарела")
                continue
            ok = mailer.send(info["to"], info["subject"], info["body"], cfg, log,
                             in_reply_to=info.get("msgid"))
            if ok:
                _answer(cq["id"], "Ответ улетел 📤")
                _mark(cq, f"✅ ответ отправлен → {info['to']}")
                pending.pop(sk, None)
            else:
                _answer(cq["id"], "Ошибка отправки — смотри логи Actions")
        elif action == "n":             # человек ответит сам
            _answer(cq["id"], "Ок, отвечаешь сам")
            _mark(cq, "✍️ отвечаешь сам")
            pending.pop(sk, None)
        elif action == "x":
            agency["status"] = "skipped"
            _answer(cq["id"], "Пропустили 👌")
            _mark(cq, "🚫 пропущено")
            log(f"skip: {agency['name']}")
        elif action == "s":
            if agency.get("status") == "sent":
                _answer(cq["id"], "Уже отправляли этому агентству")
                continue
            ok = mailer.send(agency["email"], info["subject"], info["body"], cfg, log)
            if ok:
                agency["status"] = "sent"
                agency["sent_ts"] = time.time()
                _answer(cq["id"], "КП улетело 📧")
                _mark(cq, f"✅ отправлено → {agency['email']}")
            else:
                _answer(cq["id"], "Ошибка отправки — смотри логи Actions")
    return new_offset
