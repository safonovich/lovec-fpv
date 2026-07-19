"""Один прогон (GitHub Actions по расписанию):
кнопки → раз в день порция новых агентств (обогащение + КП) → Telegram → состояние.

Запуски каждые ~15 мин нужны в первую очередь ради кнопок:
нажал «Отправить» — письмо уйдёт при ближайшем прогоне (до ~15-25 мин)."""

from __future__ import annotations

import datetime
import sys
import time
import tomllib
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fpv import callbacks, enrich, inbox, kp, mailer, notify, store

MSK = zoneinfo.ZoneInfo("Europe/Moscow")


def log(msg: str) -> None:
    print(f"[fpv] {msg}", flush=True)


def main() -> None:
    cfg = tomllib.loads(
        (Path(__file__).parent / "config.toml").read_text(encoding="utf-8"))
    b = cfg["bot"]

    now = datetime.datetime.now(MSK)
    h0, h1 = b.get("active_hours_msk", [9, 22])
    if not (h0 <= now.hour < h1):
        log(f"вне рабочих часов ({now:%H:%M} МСК) — спим")
        return

    agencies: list = store.load("agencies.json", [])
    pending: dict = store.prune_pending(store.load("pending.json", {}))
    state: dict = store.load("tg_state.json", {"offset": 0, "last_leads_date": ""})
    if not agencies:
        log("agencies.json пуст — заполни базу (см. README)")
        return

    # 1. Кнопки: отправка КП / пропуск
    state["offset"] = callbacks.process(
        pending, agencies, state.get("offset", 0), cfg, log)

    # 2. Входящие: ответы агентств → черновик Claude → карточка с кнопкой
    if cfg.get("negotiation", {}).get("enabled", True):
        state["imap_uid"] = inbox.check(
            agencies, pending, state.get("imap_uid", 0), cfg, log)

    # 3. Фоллоу-апы: нет ответа N дней → одно вежливое напоминание (автоматом)
    fu = cfg.get("followup", {})
    if fu.get("enabled"):
        cutoff = time.time() - fu.get("after_days", 4) * 86400
        for a in agencies:
            if (a.get("status") == "sent" and not a.get("followup_ts")
                    and 0 < a.get("sent_ts", 0) < cutoff):
                body = fu["template"].format(
                    agency=a["name"], portfolio=cfg["kp"]["portfolio_url"],
                    author=cfg["kp"]["author_name"], phone=cfg["kp"]["author_phone"])
                subj = "Re: " + cfg["kp"]["subject"].format(agency=a["name"])
                if mailer.send(a["email"], subj, body, cfg, log):
                    a["followup_ts"] = time.time()
                    notify.send_service(f"📮 Фоллоу-ап ушёл: {a['name']}", log)

    # 4. Порция новых лидов — раз в день, после lead_hour_msk
    today = now.strftime("%Y-%m-%d")
    if state.get("last_leads_date") != today and now.hour >= b.get("lead_hour_msk", 10):
        fresh = [a for a in agencies if a.get("status", "new") == "new"]
        batch = fresh[:b.get("daily_leads", 3)]
        if not batch:
            if state.get("base_empty_warned") != today:
                notify.send_service(
                    "📭 Новые агентства кончились — пополни data/agencies.json "
                    "и запусти workflow Collect.", log)
                state["base_empty_warned"] = today
        for a in batch:
            found = enrich.enrich(a, log)
            for f in ("email", "phone", "tg", "wa"):
                if found.get(f) and not a.get(f):
                    a[f] = found[f]
            subject, body = kp.make(a, found.get("site_text", ""), cfg, log)
            notify.send_lead(a, subject, body, pending, log,
                             cfg["kp"].get("portfolio_url", ""))
            a["status"] = "offered"
            log(f"lead: {a['name']}")
        state["last_leads_date"] = today
        log(f"выдано лидов: {len(batch)}, осталось новых: {len(fresh) - len(batch)}")

    # 5. Состояние (workflow закоммитит data/ обратно)
    store.save("agencies.json", agencies)
    store.save("pending.json", pending)
    store.save("tg_state.json", state)


if __name__ == "__main__":
    main()
