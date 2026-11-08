"""Derive deals.json, submission.json and alerts.json from deal_detail.json.

Run: python -m prism.derive  (the "regenerate the fixture first" step of the contract rule).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from prism.contract import AlertFeedItem, DealDetail, Submission, parse_sim_time

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def derive(details: dict[str, DealDetail]) -> dict[str, Any]:
    deals = [d.summary().model_dump(mode="json") for d in details.values()]

    history = sorted(
        {
            link
            for d in details.values()
            if d.fraud.flagged
            for link in d.fraud.linked_deals
            if link.startswith("H-")
        }
    )
    submission = Submission(
        team="Prism",
        set="sample",
        deals={folder: d.to_submission_deal() for folder, d in details.items()},
        history_flags=history,
    ).model_dump(mode="json")

    raw = [(folder, a) for folder, d in details.items() for a in d.alerts]
    raw.sort(key=lambda fa: parse_sim_time(fa[1].at))
    feed = [
        AlertFeedItem(id=f"alert-{i:03d}", deal=folder, **a.model_dump())
        for i, (folder, a) in enumerate(raw, start=1)
    ]
    feed.reverse()  # newest first
    alerts = [a.model_dump(mode="json") for a in feed]

    return {"deals.json": deals, "submission.json": submission, "alerts.json": alerts}


def load_details() -> dict[str, DealDetail]:
    raw = json.loads((FIXTURES / "deal_detail.json").read_text(encoding="utf-8"))
    return TypeAdapter(dict[str, DealDetail]).validate_python(raw)


def main() -> None:
    for name, content in derive(load_details()).items():
        text = json.dumps(content, indent=2, ensure_ascii=False) + "\n"
        with open(FIXTURES / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"wrote fixtures/{name}")


if __name__ == "__main__":
    main()
