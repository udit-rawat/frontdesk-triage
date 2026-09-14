"""Deterministic triage rules.

apply_policy enforces the constraints that must hold regardless of model output:
incident detection, the routing table, and the no-deadline rule.

rule_triage performs keyword-only triage for when the model is unreachable or
returns output that cannot be validated.
"""
import re

from app.schema import PRIORITY_RANK, Triage

# Category -> owning desk.
ROUTING = {
    "Sales": "Sales Team",
    "Support": "Client Success",
    "Billing": "Finance",
    "Technical": "Engineering",
    "Other": "Client Success",
}

# An incident is exposed data or work blocked right now. Incidents route to
# Engineering regardless of which desk owns the category.
DATA_EXPOSURE = re.compile(
    r"\b(wrong workspace|wrong (?:folder|account|channel|recipient)|"
    r"remov(?:e|ing) access|revoke access|data (?:breach|leak|exposure)|"
    r"leaked|exposed|unauthori[sz]ed access|sent to the wrong|"
    r"personal data|customer (?:contact information|contact details|records) .{0,30}(?:wrong|public)|"
    r"(?:former|ex[- ]|previous|departed|offboard(?:ed)?)\s+(?:contractor|employee|staff|vendor|agency|user)|"
    r"still (?:has|have|appears to have|retains) (?:admin |full |account )?access)\b",
    re.I,
)
OUTAGE = re.compile(
    r"\b(unavailable|is down|went down|outage|cannot access|can't access|"
    r"unable to (?:access|log ?in)|not loading|broken since|locked out|"
    r"down since|no access to)\b",
    re.I,
)
NO_DEADLINE = re.compile(
    r"\b(no deadline|not urgent|no rush|whenever|future (?:update|release)|"
    r"collecting ideas|nice to have|backlog|someday)\b",
    re.I,
)
BILLING = re.compile(r"\b(invoice|invoiced|charge[ds]?|billing|billed|refund|payment|overcharg|double[- ]charg)\b", re.I)
SALES = re.compile(
    r"\b(pricing|price|quote|proposal|timeline|interested in|cost|demo|new project|"
    r"saw your (?:company|website)|show us how|speak next week|book a (?:call|meeting)|"
    r"consultation|scope of work)\b",
    re.I,
)
TECH = re.compile(r"\b(bug|error|api|integration|dashboard|portal|automat|deploy|feature|dark mode|font)\b", re.I)
# Timeframes a draft must never commit the company to. The prompt forbids these, but a
# prompt is a request and not a guarantee, so the output is checked as well. The draft is
# not rewritten: it is flagged for the person who is already reading it before they send.
TIMEFRAME = re.compile(
    r"\b(within (?:the )?(?:next )?(?:hour|day|24 hours|48 hours|\d+ (?:minutes|hours|days))|"
    r"by (?:tomorrow|today|end of (?:day|week)|close of business|eod|cob|monday|tuesday|"
    r"wednesday|thursday|friday)|"
    r"in the next \d+|in \d+ (?:minutes|hours|days)|"
    r"shortly|first thing|later today|this afternoon|by then|within the day)\b",
    re.I,
)
NOW = re.compile(r"\b(immediate|immediately|urgent|asap|as soon as possible|right now|today|this morning)\b", re.I)


def _bump_to(triage: Triage, priority: str) -> bool:
    """Raise priority to at least `priority`. Returns True if it changed."""
    if PRIORITY_RANK[triage.priority] < PRIORITY_RANK[priority]:
        triage.priority = priority
        return True
    return False


def apply_policy(triage: Triage, text: str) -> tuple[Triage, list[str]]:
    """Apply the constraints that hold regardless of what the model returned."""
    notes: list[str] = []
    incident = False

    if DATA_EXPOSURE.search(text):
        incident = True
        if _bump_to(triage, "Urgent"):
            notes.append("Policy: possible data exposure, priority raised to Urgent.")
    elif OUTAGE.search(text):
        incident = True
        target = "Urgent" if NOW.search(text) else "High"
        if _bump_to(triage, target):
            notes.append(f"Policy: service outage, priority raised to {target}.")

    # A stated non-deadline describes the sender's expectation rather than the
    # consequence, so it settles a Medium into a Low and never lowers a High or an
    # Urgent. Genuine incidents are reported with "no rush" attached often enough
    # that this distinction matters.
    if NO_DEADLINE.search(text) and not incident and triage.priority == "Medium":
        notes.append("Policy: sender states no deadline, priority settled from Medium to Low.")
        triage.priority = "Low"

    # Routing is deterministic; the model's own owner pick serves as a cross-check
    # so a disagreement is recorded rather than silently accepted.
    expected = "Engineering" if incident else ROUTING[triage.category]
    if triage.owner != expected:
        notes.append(f"Policy: routing corrected from {triage.owner} to {expected}.")
        triage.owner = expected

    promise = TIMEFRAME.search(triage.draft_reply)
    if promise:
        notes.append(
            f'Draft commits to a timeframe ("{promise.group(0)}"). Confirm the team can meet it '
            "before sending, or remove it."
        )

    if incident and triage.category == "Other":
        notes.append("Policy: incident re-categorised from Other to Support.")
        triage.category = "Support"

    return triage, notes


def rule_triage(text: str) -> Triage:
    """Keyword-only triage. Requires no network and no model."""
    if DATA_EXPOSURE.search(text):
        category, priority, reason = "Support", "Urgent", "Possible exposure of customer data."
    elif OUTAGE.search(text):
        category = "Technical"
        priority = "Urgent" if NOW.search(text) else "High"
        reason = "Service is unavailable and work is blocked."
    elif BILLING.search(text):
        category, priority, reason = "Billing", "High", "Money is in dispute before a payment date."
    elif SALES.search(text):
        category, priority, reason = "Sales", "High", "Inbound commercial interest, response speed affects the deal."
    elif TECH.search(text):
        category, priority, reason = "Technical", "Medium", "Product work with no stated deadline."
    else:
        category, priority, reason = "Other", "Medium", "Needs a human read to classify."

    if NO_DEADLINE.search(text):
        priority, reason = "Low", "Request explicitly states there is no deadline."

    first = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    summary = (first[:157] + "...") if len(first) > 160 else first

    triage = Triage(
        summary=summary,
        category=category,
        priority=priority,
        priority_reason=reason,
        owner=ROUTING[category],
        confidence=0.35,
        draft_reply="pending",
    )
    triage = apply_policy(triage, text)[0]

    # Written after the overrides so the desk named in the reply is the desk the
    # request was actually routed to.
    triage.draft_reply = (
        "Thank you for getting in touch. We have received your request and logged it with "
        f"our {triage.owner} team. Someone will review the detail and come back to you with "
        "a clear next step. If anything changes in the meantime, reply here and it will be "
        "added to the same thread."
    )
    return triage
