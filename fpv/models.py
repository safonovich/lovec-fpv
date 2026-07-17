"""Общие модели. Агентство храним как dict в data/agencies.json:
{
  "id": "incom",              # уникальный слаг
  "name": "ИНКОМ-Недвижимость",
  "site": "https://www.incom.ru",
  "email": null,              # заполняет collect.py или вручную
  "phone": null,              # +7... — используется и для WhatsApp
  "tg": null,                 # @username агентства, если есть
  "district": "Москва",
  "note": "",                 # заметки коллеги
  "status": "new",            # new | offered | sent | skipped | replied
  "sent_ts": null
}
"""
