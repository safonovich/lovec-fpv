"""Генерация КП: LLM (Grok/Claude/любой из [llm]) персонализирует письмо
под агентство. Fail-open: без ключа/при ошибке — шаблон из config.toml."""

from __future__ import annotations

import json

from fpv import llm

SYSTEM = """Ты пишешь короткие коммерческие письма для FPV-пилота из Москвы.
Он снимает недвижимость FPV-дроном: динамичный пролёт одним кадром, без
склеек — фасад, все комнаты, участок, вид на район. Такое видео выделяет
объявление и собирает больше просмотров и обращений, чем фото.

Его форматы и цены:
{prices}

Условия оплаты: {payment}
Упомяни оплату одной короткой строкой в конце письма.
Криптовалюту, скидки и торг в письме НЕ упоминай.

Напиши письмо агентству недвижимости (данные ниже). Требования:
- 100–170 слов, деловой, но живой тон, без канцелярита и воды
- 1 конкретная деталь про само агентство (из справки о нём), без лести
- чем полезно ИМ: объект с видеотуром быстрее собирает показы
- из форматов упомяни ТОЛЬКО 1–2 подходящих этому агентству
  (элитка/загород → кино-уровень или FPV-видеотур; массовый сегмент →
  FPV-видеотур или классический дрон), цены со словом «от»
- никакого технического жаргона: модели камер и дронов НЕ называть
  (слово Blackmagic можно), продавай результат, а не технику
- ссылка на портфолио с примерами: {portfolio}
- подпись: {author}, {phone}
- НЕ выдумывай факты про агентство, которых нет в справке
- обращение нейтральное («Здравствуйте!»), без «Уважаемые господа»

Ответь СТРОГО одним JSON-объектом, без пояснений до и после:
{{"subject": "тема письма, до 60 знаков", "body": "текст письма с переносами строк"}}"""


def _template(agency: dict, kp_cfg: dict) -> tuple[str, str]:
    subject = kp_cfg["subject"].format(agency=agency["name"])
    body = kp_cfg["template"].format(
        agency=agency["name"], portfolio=kp_cfg["portfolio_url"],
        author=kp_cfg["author_name"], phone=kp_cfg["author_phone"],
        prices=kp_cfg.get("prices", ""), payment=kp_cfg.get("payment", ""))
    return subject, body


def make(agency: dict, site_text: str, cfg: dict, log) -> tuple[str, str]:
    """Возвращает (subject, body)."""
    kp_cfg = cfg["kp"]
    system = SYSTEM.format(portfolio=kp_cfg["portfolio_url"],
                           author=kp_cfg["author_name"],
                           phone=kp_cfg["author_phone"],
                           prices=kp_cfg.get("prices", ""),
                           payment=kp_cfg.get("payment", ""))
    info = (f"Агентство: {agency['name']}\nСайт: {agency.get('site','')}\n"
            f"Заметка: {agency.get('note','')}\n"
            f"Справка с сайта: {site_text or 'нет данных'}")
    txt = llm.chat(system, info, cfg, log)
    if not txt:
        log("kp: LLM недоступен — шаблон")
        return _template(agency, kp_cfg)
    try:
        v = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        return str(v["subject"])[:120], str(v["body"])
    except Exception as e:
        log(f"kp: не разобрал ответ LLM ({e}) — шаблон")
        return _template(agency, kp_cfg)
