"""The ordered list of model attempts.

Each entry is one provider and model. They are tried in order, so the list runs from
the fastest reliable option to the last resort. Two entries on the same provider
protect against a bad model; an entry on a second provider protects against the
provider itself being down, which is the failure a single-vendor list cannot survive.

An entry whose API key is unset is skipped, so the application runs with whatever
keys are configured.
"""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Attempt:
    label: str
    base_url: str
    api_key: str
    model: str
    options: dict = field(default_factory=dict)


def ladder() -> list[Attempt]:
    """Build the attempt list from the environment. Order matters."""
    groq_key = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY") or ""
    groq_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    gemini_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

    candidates = [
        Attempt("groq", groq_url, groq_key, os.getenv("PRIMARY_MODEL", "openai/gpt-oss-120b")),
        Attempt("groq", groq_url, groq_key, os.getenv("FALLBACK_MODEL", "openai/gpt-oss-20b")),
        # Gemini 3.x spends its token budget on reasoning before emitting the answer, so
        # it needs a low reasoning effort to stay quick and a budget it cannot exhaust.
        Attempt("google", gemini_url, gemini_key, os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
                {"reasoning_effort": os.getenv("GEMINI_REASONING_EFFORT", "low")}),
    ]
    return [c for c in candidates if c.api_key]
