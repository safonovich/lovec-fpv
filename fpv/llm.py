"""Единый LLM-клиент. Провайдер выбирается в [llm] config.toml:
- "anthropic" — Claude API
- "openai"   — любой OpenAI-совместимый API: OpenRouter, Grok (xAI),
               DeepSeek, LM Studio и т.д. (api_base + model)
Для OpenRouter поддерживается цепочка запасных моделей (fallback_models):
бесплатные модели бывают перегружены — тогда пробуем следующую.
Ключ — в секрете LLM_API_KEY (ANTHROPIC_API_KEY тоже подхватится)."""

from __future__ import annotations

import os

import requests


def _err_text(e: Exception) -> str:
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        body = " — " + str(getattr(resp, "text", ""))[:200]
    return f"{e}{body}"


def chat(system: str, user: str, cfg: dict, log, max_tokens: int = 700) -> str | None:
    """Возвращает текст ответа модели или None (нет ключа/ошибка — fail-open)."""
    l = cfg.get("llm", {})
    key = (os.environ.get("LLM_API_KEY") or
           os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not l.get("enabled", True) or not key:
        return None

    if l.get("provider", "anthropic") == "anthropic":
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": l.get("model", "claude-haiku-4-5"),
                      "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=60)
            r.raise_for_status()
            return r.json()["content"][0]["text"]
        except Exception as e:
            log(f"llm(anthropic): {_err_text(e)}")
            return None

    # openai-совместимые, с цепочкой запасных моделей
    base = l.get("api_base", "https://openrouter.ai/api/v1").rstrip("/")
    models = [l.get("model", "")] + list(l.get("fallback_models", []))
    for m in [x for x in models if x]:
        try:
            r = requests.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "content-type": "application/json"},
                json={"model": m, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=60)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            if txt and txt.strip():
                return txt
            log(f"llm({m}): пустой ответ — пробую следующую")
        except Exception as e:
            log(f"llm({m}): {_err_text(e)}")
    return None
