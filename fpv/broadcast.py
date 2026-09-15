"""Массовая рассылка одного письма по базе (режим «кампания»).

Отличие от обычного контура: письмо не пишет нейросеть под каждого —
берётся один шаблон из config.toml [broadcast], подставляется название
агентства. Уходит порциями daily_limit в день с паузой между письмами,
каждое письмо — отдельным сообщением (получатели друг друга не видят).

Запуск: кнопка «📨 Рассылка» в TG → превью → «▶️ Запустить».
Остановка: «⏹ Стоп рассылка».
"""

from __future__ import annotations

import os
import time

import requests

from fpv import mailer, notify


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/{method}"


# Мусор, который собирается парсером с сайтов: почты хостеров, заглушки,
# технические ящики. На такие адреса письмо уходить не должно.
BAD_PARTS = (
    "beget.", "reg.ru", "nic.ru", "timeweb", "sweb.ru", "masterhost",
    "example.", "domain.ru", "sitename", "yourmail", "test@", "ivanov@mail.ru",
    "noreply", "no-reply", "@sentry", "wixpress", "@wix.com",
)


def _usable(email: str) -> bool:
    e = email.strip().lower()
    if "@" not in e or "." not in e.split("@")[-1]:
        return False
    return not any(p in e for p in BAD_PARTS)


def targets(agencies: list[dict], cfg: dict) -> list[dict]:
    """Кому ещё не отправляли: живой email, не sent/skipped/replied, без дублей."""
    skip = {"sent", "skipped", "replied"}
    seen, out = set(), []
    for a in agencies:
        if a.get("status") in skip or a.get("broadcast_ts"):
            continue
        email = (a.get("email") or "").strip()
        if not _usable(email):
            continue
        key = email.lower()
        if key in seen:                      # один адрес — одно письмо
            continue
        seen.add(key)
        out.append(a)
    return out


def render(a: dict, cfg: dict) -> tuple[str, str]:
    bc, kp = cfg["broadcast"], cfg["kp"]
    fields = dict(agency=a.get("name", ""),
                  portfolio=kp.get("portfolio_url", ""),
                  author=kp.get("author_name", ""),
                  phone=kp.get("author_phone", ""))
    return bc["subject"].format(**fields), bc["body"].format(**fields)


def preview(agencies: list[dict], cfg: dict, log) -> None:
    """Показать в TG, как выглядит письмо, и спросить подтверждение."""
    queue = targets(agencies, cfg)
    if not queue:
        notify.send_service("📭 Рассылать некому: у всех агентств с почтой "
                            "уже стоит статус «отправлено/пропущено».", log)
        return
    subject, body = render(queue[0], cfg)
    limit = cfg["broadcast"].get("daily_limit", 15)
    days = (len(queue) + limit - 1) // limit
    text = (f"📨 <b>Рассылка — превью</b>\n"
            f"Получателей в очереди: <b>{len(queue)}</b> · по {limit} в день "
            f"(~{days} дн.)\n"
            f"Первый: {notify._esc(queue[0].get('name',''))} → "
            f"{notify._esc(queue[0].get('email',''))}\n\n"
            f"<b>Тема:</b> {notify._esc(subject)}\n\n"
            f"<code>{notify._esc(body[:900])}</code>")
    kb = {"inline_keyboard": [[{"text": "▶️ Запустить", "callback_data": "bcgo|1"},
                               {"text": "✖️ Отмена", "callback_data": "bcno|1"}]]}
    try:
        requests.post(_api("sendMessage"), json={
            "chat_id": os.environ.get("TG_CHAT_ID", ""), "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": kb}, timeout=20)
    except Exception as e:
        log(f"broadcast(preview): {e}")


def run_batch(agencies: list[dict], cfg: dict, log) -> int:
    """Отправить дневную порцию. Возвращает, сколько ушло."""
    bc = cfg["broadcast"]
    limit = bc.get("daily_limit", 15)
    pause = bc.get("pause_sec", 45)
    queue = targets(agencies, cfg)[:limit]
    started, budget = time.time(), bc.get("max_seconds", 780)
    sent = 0
    for i, a in enumerate(queue):
        if time.time() - started > budget:   # не упереться в timeout воркфлоу
            log(f"broadcast: бюджет времени исчерпан, остаток уйдёт позже")
            break
        subject, body = render(a, cfg)
        if mailer.send(a["email"], subject, body, cfg, log):
            a["status"] = "sent"
            a["sent_ts"] = time.time()
            a["broadcast_ts"] = time.time()
            sent += 1
        else:
            log(f"broadcast: не ушло → {a.get('name')} ({a.get('email')})")
        if i < len(queue) - 1:
            time.sleep(pause)
    left = len(targets(agencies, cfg))
    if sent:
        notify.send_service(
            f"📨 Рассылка: отправлено {sent} писем. Осталось в очереди: {left}.\n"
            f"Копии — в «Отправленных» твоей почты.", log)
    if not left:
        notify.send_service("✅ Рассылка завершена — база пройдена полностью.", log)
    return sent
