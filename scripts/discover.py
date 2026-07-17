"""Автопополнение базы: тянем агентства недвижимости Москвы из OpenStreetMap
(Overpass API — бесплатно, без ключей) и добавляем новые в data/agencies.json.
Запускается раз в неделю workflow'ом Discover (или вручную)."""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fpv import store

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:120];
area["ISO3166-2"="RU-MOW"][admin_level=4]->.msk;
nwr["office"="estate_agent"](area.msk);
out tags;
"""
MAX_NEW_PER_RUN = 30   # чтобы база не разбухала мусором за один раз


def log(msg: str) -> None:
    print(f"[discover] {msg}", flush=True)


def _domain(url: str) -> str:
    d = re.sub(r"^https?://", "", (url or "").lower()).split("/")[0]
    return d.removeprefix("www.")


def _slug(name: str, site: str) -> str:
    d = _domain(site)
    if d:
        return re.sub(r"[^a-z0-9]", "", d.split(".")[0]) or "osm" + hashlib.sha1(d.encode()).hexdigest()[:8]
    return "osm" + hashlib.sha1(name.lower().encode()).hexdigest()[:8]


def main() -> None:
    agencies: list = store.load("agencies.json", [])
    known_ids = {a["id"] for a in agencies}
    known_domains = {_domain(a.get("site", "")) for a in agencies} - {""}
    known_names = {a["name"].lower() for a in agencies}

    try:
        r = requests.post(OVERPASS, data={"data": QUERY}, timeout=180)
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as e:
        log(f"Overpass не ответил — {e}")
        return
    log(f"OSM вернул объектов: {len(elements)}")

    added = 0
    for el in elements:
        if added >= MAX_NEW_PER_RUN:
            break
        t = el.get("tags", {})
        name = t.get("name", "").strip()
        site = t.get("website") or t.get("contact:website") or ""
        email = t.get("email") or t.get("contact:email")
        phone = t.get("phone") or t.get("contact:phone")
        if not name or len(name) < 3:
            continue
        if not site and not email:          # без сайта и почты КП слать некуда
            continue
        if name.lower() in known_names or (_domain(site) and _domain(site) in known_domains):
            continue
        aid = _slug(name, site)
        while aid in known_ids:
            aid += "x"
        agencies.append({
            "id": aid, "name": name, "site": site.rstrip("/") or None,
            "email": email, "phone": phone, "tg": None,
            "district": "Москва", "note": "найдено автоматически (OSM)",
            "status": "new", "sent_ts": None,
        })
        known_ids.add(aid)
        known_names.add(name.lower())
        if _domain(site):
            known_domains.add(_domain(site))
        added += 1
        log(f"+ {name} ({site or email})")

    store.save("agencies.json", agencies)
    log(f"добавлено новых: {added}, всего в базе: {len(agencies)}")

    # если запущено из Actions с секретами — коротко отчитаться в Telegram
    if added and os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID"):
        from fpv import notify
        notify.send_service(f"🔎 База пополнена: +{added} агентств (теперь {len(agencies)})", log)


if __name__ == "__main__":
    main()
