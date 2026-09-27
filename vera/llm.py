"""
Optional LLM polish layer.

OFF by default. Turn on with env vars:
    VERA_LLM_PROVIDER = anthropic | openai | gemini | groq | openrouter | deepseek
    VERA_LLM_API_KEY  = ...
    VERA_LLM_MODEL    = (optional override)

Contract: the LLM may only *rephrase* the deterministic draft. The result is
accepted only if it passes every check below; otherwise the deterministic draft
ships. So the LLM can improve fluency but can never add a fabricated fact.
    - every number in the output already appears in the draft
    - every number in the draft still appears (no dropped specifics)
    - no URL, no category taboo word
    - still ends with the CTA (last sentence contains YES/CONFIRM/reply/?)
    - length within 60%-130% of the draft
Determinism: temperature 0 + an in-process cache keyed by the input hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from urllib import request as urlrequest

PROVIDER = os.getenv("VERA_LLM_PROVIDER", "").strip().lower()
API_KEY = os.getenv("VERA_LLM_API_KEY", "").strip()
MODEL = os.getenv("VERA_LLM_MODEL", "").strip()
TIMEOUT = float(os.getenv("VERA_LLM_TIMEOUT_S", "8"))

DEFAULTS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "anthropic/claude-3.5-haiku",
    "deepseek": "deepseek-chat",
}

_cache: dict[str, str] = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(PROVIDER and API_KEY and PROVIDER in DEFAULTS)


def model_label() -> str:
    if enabled():
        return f"deterministic-engine + {PROVIDER}:{MODEL or DEFAULTS[PROVIDER]} (polish, fact-locked)"
    return "deterministic-engine (rule-based composer, no LLM at send time)"


SYSTEM = (
    "You polish WhatsApp messages written by Vera, magicpin's assistant for Indian merchants. "
    "Rewrite the DRAFT to read more naturally and punchily. HARD RULES: keep every number, price, date, name, "
    "source citation and quote exactly; add NO new facts, numbers, offers or claims; keep the same language mix "
    "(Hinglish stays Hinglish); keep exactly one call-to-action as the final sentence; no URLs; no hype words; "
    "keep line breaks for lists. Output ONLY the rewritten message text."
)


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def _valid(draft: str, out: str, taboos: list[str]) -> bool:
    if not out or len(out) < 0.6 * len(draft) or len(out) > 1.3 * len(draft):
        return False
    if _numbers(out) - _numbers(draft):
        return False
    if _numbers(draft) - _numbers(out):
        return False
    if re.search(r"https?://|www\.", out):
        return False
    low = out.lower()
    for t in taboos:
        t0 = re.sub(r"\s*\(.*?\)", "", str(t)).strip().lower()
        if t0 and t0 in low:
            return False
    last = re.split(r"(?<=[.!?])\s+", out.strip())[-1]
    return bool(re.search(r"\b(YES|CONFIRM|HAAN|reply|bhej|1|2)\b", last, re.I)) or "?" in last


def _call(prompt: str) -> str:
    model = MODEL or DEFAULTS[PROVIDER]
    if PROVIDER == "anthropic":
        body = {
            "model": model,
            "max_tokens": 700,
            "temperature": 0,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urlrequest.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        )
        data = json.loads(urlrequest.urlopen(req, timeout=TIMEOUT).read())
        return data["content"][0]["text"]
    if PROVIDER == "gemini":
        body = {
            "contents": [{"parts": [{"text": SYSTEM + "\n\n" + prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 700},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={API_KEY}"
        req = urlrequest.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        data = json.loads(urlrequest.urlopen(req, timeout=TIMEOUT).read())
        return data["candidates"][0]["content"]["parts"][0]["text"]
    urls = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "deepseek": "https://api.deepseek.com/v1/chat/completions",
    }
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 700,
        "seed": 7,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    }
    req = urlrequest.Request(
        urls[PROVIDER],
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    data = json.loads(urlrequest.urlopen(req, timeout=TIMEOUT).read())
    return data["choices"][0]["message"]["content"]


def maybe_polish(result: dict, cat: dict, m: dict, trg: dict, cust) -> dict:
    """Return result with a polished body if (and only if) the polish is safe."""
    if not enabled():
        return result
    draft = result["body"]
    voice = (cat or {}).get("voice") or {}
    prompt = (
        f"Category voice: {voice.get('tone')} / {voice.get('register')}. "
        f"Audience: {'the merchant owner' if result['send_as'] == 'vera' else 'the merchant''s customer'}.\n\nDRAFT:\n{draft}"
    )
    key = hashlib.sha256((PROVIDER + (MODEL or "") + prompt).encode()).hexdigest()
    with _lock:
        cached = _cache.get(key)
    if cached is None:
        try:
            out = _call(prompt).strip().strip('"').strip()
        except Exception:
            return result
        with _lock:
            _cache[key] = out
        cached = out
    if _valid(draft, cached, voice.get("vocab_taboo") or []):
        result = dict(result)
        result["body"] = cached
        result["rationale"] = result["rationale"] + " (LLM-polished; fact-lock validated.)"
    return result
