"""Triage call and its degradation path.

  strict JSON schema  ->  repair retry  ->  next provider  ->  keyword rules

Each stage returns a valid Triage, so the caller never has to handle a failure.
"""
import os
import time

from openai import OpenAI
from pydantic import ValidationError

from app.policy import TIMEFRAME, apply_policy, rule_triage
from app.prompt import DERISK_PROMPT, REPAIR_PROMPT, SYSTEM_PROMPT
from app.providers import Attempt, ladder
from app.schema import TRIAGE_JSON_SCHEMA, Triage

TIMEOUT_S = float(os.getenv("LLM_TIMEOUT", "30"))
# Reasoning models spend this budget before emitting the answer, so a tight cap shows
# up as truncated JSON rather than as an error.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

# Groq's edge rejects raw urllib and curl user agents with a 403, which the OpenAI
# SDK avoids. Every provider in the ladder speaks the same OpenAI-compatible API, so
# one client class covers all of them.
_clients: dict[tuple[str, str], OpenAI] = {}


def client(attempt: Attempt) -> OpenAI:
    key = (attempt.base_url, attempt.api_key)
    if key not in _clients:
        _clients[key] = OpenAI(
            api_key=attempt.api_key, base_url=attempt.base_url, timeout=TIMEOUT_S, max_retries=1
        )
    return _clients[key]


def _call(attempt: Attempt, messages: list[dict]) -> str:
    completion = client(attempt).chat.completions.create(
        model=attempt.model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "triage", "strict": True, "schema": TRIAGE_JSON_SCHEMA},
        },
        temperature=0,
        max_tokens=MAX_TOKENS,
        **attempt.options,
    )
    return completion.choices[0].message.content or ""


def _attempt(attempt: Attempt, text: str) -> tuple[Triage | None, str | None]:
    """One model attempt with a single in-conversation repair retry."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Inbound request:\n\n{text}"},
    ]
    raw = _call(attempt, messages)
    try:
        return Triage.model_validate_json(raw), None
    except (ValidationError, ValueError) as first_error:
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REPAIR_PROMPT.format(error=str(first_error)[:400])},
        ]
        raw = _call(attempt, messages)
        try:
            return Triage.model_validate_json(raw), "Model output was repaired on a second pass."
        except (ValidationError, ValueError) as second_error:
            return None, f"{type(second_error).__name__}: {str(second_error)[:160]}"


def _rewrite_draft(attempt: Attempt, result: Triage, phrase: str) -> bool:
    """Strip a timeframe commitment from a draft. True if the rewrite came back clean.

    The models keep reaching for "shortly" and "within the hour" however firmly the prompt
    forbids it, so the output is checked and repaired rather than trusted.
    """
    try:
        completion = client(attempt).chat.completions.create(
            model=attempt.model,
            messages=[{"role": "user",
                       "content": DERISK_PROMPT.format(phrase=phrase, draft=result.draft_reply)}],
            temperature=0,
            max_tokens=MAX_TOKENS,
            **attempt.options,
        )
    except Exception:
        return False
    rewritten = (completion.choices[0].message.content or "").strip().strip('"')
    if not rewritten or TIMEFRAME.search(rewritten):
        return False
    result.draft_reply = rewritten
    return True


def triage(text: str) -> dict:
    """Triage one request. Never raises."""
    started = time.time()
    notes: list[str] = []
    result: Triage | None = None
    answered_by: Attempt | None = None

    attempts = ladder()
    for index, attempt in enumerate(attempts):
        try:
            result, note = _attempt(attempt, text)
        except Exception as exc:  # network, auth, rate limit, provider outage
            notes.append(f"{attempt.model} unavailable ({type(exc).__name__}).")
            continue
        if result is not None:
            answered_by = attempt
            if note:
                notes.append(note)
            if index > 0:
                notes.append(f"Earlier models failed, answered by {attempt.model} on {attempt.label}.")
            break
        notes.append(f"{attempt.model} returned output that failed validation.")

    if result is None:
        if not attempts:
            notes.append("No provider is configured.")
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
    adjusted = bool(policy_notes)

    promise = TIMEFRAME.search(result.draft_reply)
    if promise:
        adjusted = True
        if _rewrite_draft(answered_by, result, promise.group(0)):
            notes = [n for n in notes if not n.startswith("Draft commits to a timeframe")]
            notes.append(f'Rewrote a timeframe commitment ("{promise.group(0)}") out of the draft.')

    return {
        "triage": result,
        "source": "model+policy" if adjusted else "model",
        "notes": notes,
        "model": answered_by.model,
        "latency_ms": int((time.time() - started) * 1000),
    }
