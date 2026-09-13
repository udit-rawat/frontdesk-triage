"""Triage call and its degradation path.

  strict JSON schema  ->  repair retry  ->  fallback model  ->  keyword rules

Each stage returns a valid Triage, so the caller never has to handle a failure.
"""
import json
import os
import time

from openai import OpenAI
from pydantic import ValidationError

from app.policy import apply_policy, rule_triage
from app.prompt import REPAIR_PROMPT, SYSTEM_PROMPT
from app.schema import TRIAGE_JSON_SCHEMA, Triage

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "openai/gpt-oss-20b")
TIMEOUT_S = float(os.getenv("LLM_TIMEOUT", "25"))

# Groq's edge rejects raw urllib and curl user agents with a 403, which the OpenAI
# SDK avoids. The SDK also keeps the provider swappable to any OpenAI-compatible
# endpoint through LLM_BASE_URL alone.
_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        key = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY")
        if not key:
            raise RuntimeError("no API key set")
        _client = OpenAI(api_key=key, base_url=BASE_URL, timeout=TIMEOUT_S, max_retries=1)
    return _client


def _call(model: str, messages: list[dict]) -> str:
    completion = client().chat.completions.create(
        model=model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "triage", "strict": True, "schema": TRIAGE_JSON_SCHEMA},
        },
        temperature=0,
        max_tokens=900,
    )
    return completion.choices[0].message.content or ""


def _attempt(model: str, text: str) -> tuple[Triage | None, str | None]:
    """One model attempt with a single in-conversation repair retry."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Inbound request:\n\n{text}"},
    ]
    raw = _call(model, messages)
    try:
        return Triage.model_validate_json(raw), None
    except (ValidationError, ValueError) as first_error:
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REPAIR_PROMPT.format(error=str(first_error)[:400])},
        ]
        raw = _call(model, messages)
        try:
            return Triage.model_validate_json(raw), "Model output was repaired on a second pass."
        except (ValidationError, ValueError) as second_error:
            return None, f"{type(second_error).__name__}: {str(second_error)[:160]}"


def triage(text: str) -> dict:
    """Triage one request. Never raises."""
    started = time.time()
    notes: list[str] = []
    result: Triage | None = None
    used_model: str | None = None

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            result, note = _attempt(model, text)
        except Exception as exc:  # network, auth, rate limit, provider outage
            notes.append(f"{model} unavailable ({type(exc).__name__}).")
            continue
        if result is not None:
            used_model = model
            if note:
                notes.append(note)
            if model is FALLBACK_MODEL:
                notes.append(f"Primary model failed, answered by {model}.")
            break
        notes.append(f"{model} returned output that failed validation.")

    if result is None:
        result = rule_triage(text)
        notes.append("Model unavailable. Answered by the keyword rule engine, confidence is low by design.")
        return {
            "triage": result,
            "source": "rules",
            "notes": notes,
            "model": None,
            "latency_ms": int((time.time() - started) * 1000),
        }

    result, policy_notes = apply_policy(result, text)
    notes += policy_notes
    return {
        "triage": result,
        "source": "model+policy" if policy_notes else "model",
        "notes": notes,
        "model": used_model,
        "latency_ms": int((time.time() - started) * 1000),
    }
