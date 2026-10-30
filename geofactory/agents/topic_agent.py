"""Non-factual topic ideation and history-aware originality scoring."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from datetime import datetime, timezone

from geofactory.core.database import Database

IDEA_SEEDS = (
    ("How do mountain borders shape everyday life?", "Andes border regions", "Borders and communities"),
    ("Why do some cities grow around rivers?", "River cities", "Urban geography"),
    ("How does island geography affect transport?", "Island transport networks", "Islands and infrastructure"),
    ("What makes a desert settlement sustainable?", "Desert settlements", "Climate and adaptation"),
    ("How do ports connect inland economies to the world?", "Port hinterlands", "Trade and physical geography"),
)


def propose_topics(db: Database, limit: int = 5) -> list[dict[str, object]]:
    with db.connect() as conn:
        previous = [
            f"{row['topic']} {row['question']}".casefold()
            for row in conn.execute("SELECT topic, question FROM jobs").fetchall()
        ]
    proposals = []
    for question, focus, theme in IDEA_SEEDS:
        similarity = max(
            (
                max(
                    SequenceMatcher(None, question.casefold(), old).ratio(),
                    SequenceMatcher(None, focus.casefold(), old).ratio(),
                )
                for old in previous
            ),
            default=0.0,
        )
        originality = round(max(0.0, 1.0 - similarity), 2)
        proposals.append(
            {
                "question": question,
                "topic": focus,
                "educational_value": theme,
                "geographic_focus": focus,
                "likely_source_availability": "Needs evidence review; no sources claimed.",
                "visual_feasibility": "Maps, permitted stills, and original diagrams may work.",
                "originality_score": originality,
                "duplicate_warning": similarity >= 0.72,
                "score": round(0.35 * originality + 0.3 * 0.75 + 0.35 * 0.7, 2),
            }
        )
    proposals = sorted(proposals, key=lambda proposal: float(proposal["score"]), reverse=True)[:limit]
    with db.connect() as conn:
        for proposal in proposals:
            conn.execute(
                """INSERT INTO topic_proposals(topic, question, score, proposal_json, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(topic, question) DO UPDATE SET
                     score=excluded.score, proposal_json=excluded.proposal_json,
                     created_at=excluded.created_at""",
                (
                    str(proposal["topic"]), str(proposal["question"]), float(proposal["score"]),
                    json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    return proposals
