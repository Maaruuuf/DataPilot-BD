"""Thin wrapper around the Groq SDK (openai/gpt-oss-120b by default).

Every agent calls this wrapper, never the SDK directly. If no API key is configured (or a call fails)
agents fall back to deterministic logic, so the whole system still runs offline for demos and tests.
"""
import json
import logging
import re
from typing import Any, Optional

from ..config import GROQ_API_KEY, MODEL_MAIN, MODEL_FAST

log = logging.getLogger("datapilot.llm")


class LLM:
    def __init__(self, api_key: str = GROQ_API_KEY):
        self.client = None
        if api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=api_key)
            except Exception as e:  # pragma: no cover
                log.warning("Groq client could not be created: %s", e)

    @property
    def available(self) -> bool:
        return self.client is not None

    def chat(self, messages: list, model: Optional[str] = None, temperature: float = 0.2,
             json_mode: bool = False, tools: Optional[list] = None, max_tokens: int = 4096,
             reasoning_effort: str = "low"):
        """Return the raw assistant message object. Raises if the API call fails."""
        if not self.client:
            raise RuntimeError("GROQ_API_KEY is not configured")
        kwargs: dict = dict(model=model or MODEL_MAIN, messages=messages, temperature=temperature,
                            max_completion_tokens=max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = self.client.chat.completions.create(reasoning_effort=reasoning_effort, **kwargs)
        except TypeError:  # older SDK without reasoning_effort
            resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message

    def json(self, system: str, user: str, model: Optional[str] = None, temperature: float = 0.1,
             max_tokens: int = 4096) -> Optional[Any]:
        """Ask for a JSON object. Returns parsed data or None (never raises)."""
        if not self.available:
            return None
        try:
            msg = self.chat([{"role": "system", "content": system + "\nRespond with a single valid JSON object only."},
                             {"role": "user", "content": user}],
                            model=model, temperature=temperature, json_mode=True, max_tokens=max_tokens)
            return parse_json(msg.content or "")
        except Exception as e:
            log.warning("LLM JSON call failed: %s", e)
            return None

    def text(self, system: str, user: str, model: Optional[str] = None, temperature: float = 0.3,
             max_tokens: int = 4096) -> Optional[str]:
        if not self.available:
            return None
        try:
            msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                            model=model, temperature=temperature, max_tokens=max_tokens)
            return (msg.content or "").strip()
        except Exception as e:
            log.warning("LLM text call failed: %s", e)
            return None


def parse_json(text: str) -> Optional[Any]:
    """Parse JSON even if the model wrapped it in ``` fences or added prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


_llm: Optional[LLM] = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm


def set_llm(obj) -> None:
    """Swap the LLM (used by tests to inject a fake)."""
    global _llm
    _llm = obj


LANG_NAME = {"en": "English", "bn": "Bangla (বাংলা)"}

__all__ = ["LLM", "get_llm", "set_llm", "parse_json", "MODEL_MAIN", "MODEL_FAST", "LANG_NAME"]
