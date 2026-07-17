"""Единый LLM-клиент. Провайдер выбирается в [llm] config.toml:
- "anthropic" — Claude API
- "openai"   — любой OpenAI-совместимый API: Grok (xAI), DeepSeek,
               OpenRouter, LM Studio и т.д. (api_base + model)
Ключ — в секрете LLM_API_KEY (ANTHROPIC_API_KEY тоже подхватится)."""

from __future__ import annotations

import os

import requests


def chat(system: str, user: str, cfg: dict, log, max_tokens: int = 700) -> str | None:
    """Возвращает текст ответа модели или None (нет ключа/ошибка — fail-open)."""
    l = cfg.get("llm", {})
    key = (os.environ.get("LLM_API_KEY") or
           os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not l.get("enabled", True) or not key:
        return None
    provider = l.get("provider", "anthropic")
    try:
        if provider == "anthropic":
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
        else:  # openai-совместимые
            base = l.get("api_base", "https://api.x.ai/v1").rstrip("/")
            r = requests.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "content-type": "application/json"},
                json={"model": l.get("model", "grok-4.1-fast"),
                      "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=60)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"llm({provider}): {e}")
        return None
