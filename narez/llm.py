from __future__ import annotations
import json
import re

import requests


class LLMError(RuntimeError):
    pass


def chat_json(cfg: dict, system: str, user: str):
    """Запрос к локальной модели Ollama, ответ — JSON."""
    c = cfg["llm"]
    try:
        r = requests.post(
            f"{c['host']}/api/chat",
            json={
                "model": c["model"],
                "stream": False,
                "format": "json",
                "options": {"temperature": c["temperature"], "num_ctx": c["num_ctx"]},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=600,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(
            f"Не удалось связаться с Ollama ({c['host']}). Запущена ли она и скачана ли модель "
            f"'{c['model']}'? (ollama pull {c['model']})\n{e}"
        ) from e
    return parse_json(r.json()["message"]["content"])


def parse_json(text: str):
    """Достаёт JSON из ответа, даже если модель обернула его в ```."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not m:
            raise LLMError(f"Модель вернула не JSON: {text[:200]}")
        return json.loads(m.group(1))


def chat_text(cfg: dict, system: str, user: str) -> str:
    c = cfg["llm"]
    try:
        r = requests.post(
            f"{c['host']}/api/chat",
            json={
                "model": c["model"], "stream": False,
                "options": {"temperature": 0.2, "num_ctx": c["num_ctx"]},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=600,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"Ollama недоступна: {e}") from e
    return r.json()["message"]["content"].strip()
