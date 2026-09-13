"""Typed contract for a triage result."""
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

Category = Literal["Sales", "Support", "Billing", "Technical", "Other"]
Priority = Literal["Low", "Medium", "High", "Urgent"]
Owner = Literal["Sales Team", "Client Success", "Finance", "Engineering"]

PRIORITY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Urgent": 3}


class Triage(BaseModel):
    """A complete triage record. No field is optional."""

    summary: str = Field(..., description="One sentence, under 25 words.")
    category: Category
    priority: Priority
    priority_reason: str = Field(..., description="One clause naming impact and time pressure.")
    owner: Owner
    confidence: float = Field(..., ge=0.0, le=1.0)
    draft_reply: str = Field(..., description="3-5 sentences a team member could send after a read.")

    @field_validator("summary", "priority_reason", "draft_reply")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("empty text field")
        return v.strip()


class TriageResponse(BaseModel):
    """A triage record with its provenance."""

    id: str
    text: str
    triage: Triage
    source: Literal["model", "model+policy", "rules"]
    notes: list[str] = []
    model: Optional[str] = None
    latency_ms: int = 0
    created_at: str = ""


# Sent to the provider as a strict response schema. Declared here rather than derived
# from Pydantic so the enum wording matches the wording used in the system prompt.
TRIAGE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "category", "priority", "priority_reason", "owner", "confidence", "draft_reply"],
    "properties": {
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": ["Sales", "Support", "Billing", "Technical", "Other"]},
        "priority": {"type": "string", "enum": ["Low", "Medium", "High", "Urgent"]},
        "priority_reason": {"type": "string"},
        "owner": {"type": "string", "enum": ["Sales Team", "Client Success", "Finance", "Engineering"]},
        "confidence": {"type": "number"},
        "draft_reply": {"type": "string"},
    },
}
