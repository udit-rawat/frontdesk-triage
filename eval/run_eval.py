"""Run the triage pipeline over labelled cases and print a pass table.

Usage:  python -m eval.run_eval            (all cases)
        python -m eval.run_eval --mocks    (seeded requests only)
"""
import argparse
import json
import pathlib
import sys

from dotenv import load_dotenv

load_dotenv()

from app.policy import TIMEFRAME  # noqa: E402
from app.triage import triage  # noqa: E402  (env must load first)

CASES = pathlib.Path(__file__).with_name("cases.json")


def accepts(allowed: list[str], value: str) -> bool:
    """A case may accept one label, several, or any of them."""
    return "any" in allowed or value in allowed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mocks", action="store_true", help="only the seeded requests")
    args = parser.parse_args()

    cases = json.loads(CASES.read_text())
    if args.mocks:
        cases = [c for c in cases if c["id"].startswith("0")]

    rows, passed = [], 0
    for case in cases:
        out = triage(case["text"])
        t = out["triage"]
        expected = case["expect"]
        cat_ok = accepts(expected["category"], t.category)
        pri_ok = accepts(expected["priority"], t.priority)
        own_ok = accepts(expected["owner"], t.owner)
        # Some requests are genuinely ambiguous. For those the label is a coin flip and
        # the behaviour worth asserting is that the model hedges instead of committing.
        bound = expected.get("confidence_below")
        conf_ok = bound is None or t.confidence < bound
        # A drafting rule is worth asserting on every case, not just on labelled ones:
        # a reply that commits the company to a deadline is wrong whatever the category.
        promise = TIMEFRAME.search(t.draft_reply)
        draft_ok = promise is None
        ok = cat_ok and pri_ok and own_ok and conf_ok and draft_ok
        passed += ok
        rows.append(
            (case["id"], "PASS" if ok else "FAIL",
             f"{t.category}{'' if cat_ok else ' !'}",
             f"{t.priority}{'' if pri_ok else ' !'}",
             f"{t.owner}{'' if own_ok else ' !'}",
             f"{t.confidence:.2f}{'' if conf_ok else ' !'}", out["source"], f"{out['latency_ms']}ms",
             "ok" if draft_ok else f"promises {promise.group(0)!r}", case["label"])
        )

    header = ("ID", "", "CATEGORY", "PRIORITY", "OWNER", "CONF", "SOURCE", "TIME", "DRAFT", "CASE")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    line = "  ".join(h.ljust(w) for h, w in zip(header, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    print("-" * len(line))
    print(f"{passed}/{len(rows)} passed")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
