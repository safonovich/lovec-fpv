"""Источник лидов: обычный веб-поиск через Serper (Google Search API).

Зачем он нужен. OSM — это карта магазинов, она не знает отраслей: по запросу
«event-агентства Москвы» там десяток записей, и они давно выбраны. Нейросеть
по памяти выдумывает компании — половина с мёртвыми сайтами. Веб-поиск даёт
то, что нужно: живые сайты реальных компаний по отраслевому запросу.

Как работает:
  1. берёт поисковые запросы из business.toml;
  2. спрашивает у Serper выдачу (по странице за раз, курсор крутится,
     чтобы не возвращать одно и то же);
  3. выбрасывает агрегаторы, каталоги и соцсети — нужныт сайты самих компаний;
  4. заходит на сайт и снимает почту с главной и со страницы контактов.

Клю�� — в переменной окружения SERPER_API_KEY (секрет репозитория).
Без ключа модуль молча возвращает пустой список: прогон не падает,
остальные источники работают как работали.
"""

from __future__ import annotations

import os
import re
import time

import requests

API_URL = "https://google.serper.dev/search"
UA = {"User-Agent": "Mozilla/5.0 (compatible; lovec-outreach/1.0)"}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")
TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)

# Почты, которые ничего не стоят: хостеры, заглушки, технические ящики.
BAD_EMAIL_PARTS = (
    "beget.", "reg.ru", "nic.ru", "timeweb", "sweb.ru", "masterhost",
    "example.", "domain.ru", "sitename", "yourmail", "test@", "noreply",
    "no-reply", "@sentry", "wixpress", "@wix.com", "@tilda", "sentry.io",
    "godaddy", "@2x.png", ".png", ".jpg", ".gif", ".webp",
)

# Не компании, а площадки: на них писать бессмысленно.
AGGREGATORS = (
    "cian.ru", "avito.ru", "domclick.ru", "2gis.ru", "2gis.com", "yandex.",
    "ya.ru", "google.", "hh.ru", "zoon.ru", "yell.ru", "flamp.ru",
    "rusprofile.ru", "list-org.com", "spark-interfax.ru", "sbis.ru",
    "wikipedia.org", "youtube.com", "vk.com", "ok.ru", "t.me", "telegram",
    "instagram.com", "facebook.com", "dzen.ru", "vc.ru", "habr.com",
    "pikabu.ru", "irr.ru", "youla.ru", "kp.ru", "rbc.ru", "forbes.ru",
    "tproger", "blogspot", "livejournal", "pinterest", "tiktok.com",
    "novostroy", "mskguru", "poselkino", "restate.ru", "move.ru",
    "the-village", "afisha.ru", "timepad.ru", "profi.ru", "youdo.com",
)

CONTACT_PATHS = ("", "/contacts", "/contacts/", "/kontakty", "/kontakty/",
                 "/contact", "/about/contacts/")


def _domain(url: str) -> str:
    d = re.sub(r"^[a-z]+://", "", (url or "").lower()).split("/")[0]
    return d.removeprefix("www.").split(":")[0]


def _is_company_site(url: str) -> bool:
    d = _domain(url)
    if not d or "." not in d:
        return False
    return not any(bad in d for bad in AGGREGATORS)


def _usable_email(email: str) -> bool:
    e = email.lower()
    if any(p in e for p in BAD_EMAIL_PARTS):
        return False
    return len(e) < 70 and e.count("@") == 1


def _pick_email(emails: list[str], domain: str) -> str | None:
    """Почта на домене компании лучше, чем сборная солянка с чужих доменов."""
    good = [e for e in dict.fromkeys(emails) if _usable_email(e)]
    if not good:
        return None
    own = [e for e in good if e.lower().endswith("@" + domain)]
    pool = own or good
    # info@ / sales@ / hello@ полезнее, чем личная почта сотрудника
    for prefix in ("info@", "sales@", "hello@", "office@", "mail@",
                   "pr@", "partner", "zakaz@", "shop@"):
        for e in pool:
            if e.lower().startswith(prefix):
                return e
    return pool[0]


def _contacts_from_site(site: str, log, timeout: int = 12) -> str | None:
    """Пройтись по главной и типичным страницам контактов, снять почту."""
    domain = _domain(site)
    base = "https://" + domain
    found: list[str] = []
    for path in CONTACT_PATHS:
        try:
            r = requests.get(base + path, headers=UA, timeout=timeout,
                             allow_redirects=True)
            if not r.ok or "html" not in r.headers.get("content-type", "html"):
                continue
            text = TAG_RE.sub(" ", r.text[:400_000])
            found += EMAIL_RE.findall(text)
            email = _pick_email(found, domain)
            if email:
                return email
        except Exception:
            continue
    return _pick_email(found, domain)


def search_once(query: str, log, page: int = 1, num: int = 10,
                gl: str = "ru", hl: str = "ru") -> list[dict]:
    """Одна страница выдачи. Пустой список при любой ошибке."""
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        log("serper: ключ SERPER_API_KEY не задан — источник пропущен")
        return []
    try:
        r = requests.post(
            API_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "gl": gl, "hl": hl, "num": num, "page": page},
            timeout=30)
        if r.status_code == 403:
            log("serper: ключ отвергнут (403) — проверь секрет")
            return []
        if r.status_code == 429:
            log("serper: лимит запросов исчерпан (429)")
            return []
        r.raise_for_status()
        return r.json().get("organic", []) or []
    except Exception as e:
        log(f"serper: запрос не прошёл — {e}")
        return []


def find_leads(limit: int, cfg: dict, log, state: dict | None = None,
               pause: float = 0.5) -> list[dict]:
    """Главная функция источника: вернуть до `limit` кандидатов.

    Кандидат = {name, site, email, phone, note, status}. Почта может быть
    None — её потом добьёт Collect; дубли отсекает replenish.add_unique.

    `state` — любой словарь, который переживает прогоны (у нас tg_state).
    В нём хранится курсор, чтобы каждый раз не перебирать одни 0 и те же
    страницы выдачи по одному и тому же запросу.
    """
    search_cfg = (cfg or {}).get("search", {})
    queries = [q for q in search_cfg.get("queries", []) if str(q).strip()]
    if not queries:
        log("serper: в business.toml нет поисковых запросов — источник пропущен")
        return []

    gl = search_cfg.get("gl", "ru")
    hl = search_cfg.get("hl", "ru")
    max_pages = int(search_cfg.get("max_pages", 5))
    fetch_contacts = bool(search_cfg.get("fetch_contacts", True))

    state = state if state is not None else {}
    cursor = int(state.get("serper_cursor", 0))

    out: list[dict] = []
    seen_domains: set[str] = set()
    # по запросу за подход, страницы крутятся курсором
    for step in range(len(queries) * max_pages):
        if len(out) >= limit:
            break
        idx = (cursor + step) % (len(queries) * max_pages)
        query = queries[idx % len(queries)]
        page = idx // len(queries) + 1

        results = search_once(query, log, page=page, gl=gl, hl=hl)
        log(f"serper: «{query}» стр.{page} → {len(results)} результатов")
        if not results:
            continue

        for item in results:
            if len(out) >= limit:
                break
            link = item.get("link") or ""
            if not _is_company_site(link):
                continue
            domain = _domain(link)
            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            name = (item.get("title") or "").split("|")[0].split("—")[0]
            name = name.split(" - ")[0].strip()[:120]
            if len(name) < 3:
                continue

            email = _contacts_from_site(link, log) if fetch_contacts else None
            out.append({
                "name": name,
                "site": "https://" + domain,
                "email": email,
                "phone": None,
                "tg": None,
                "note": f"найдено поиском: {query}"[:200],
                "status": "new",
                "sent_ts": None,
            })
            if fetch_contacts:
                time.sleep(pause)      # не долбим чужие сайты очередью

    state["serper_cursor"] = cursor + len(queries)
    with_mail = sum(1 for a in out if a["email"])
    log(f"serper: кандидатов {len(out)}, из них с почтой {with_mail}")
    return out
