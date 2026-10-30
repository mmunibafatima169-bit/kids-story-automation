"""Import owner-provided analytics CSV rows without causal growth claims."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from geofactory.core.database import Database

REQUIRED_FIELDS = {"views", "click_through_rate", "watch_time_minutes", "subscribers_gained"}


def import_csv(db: Database, path: Path) -> int:
    inserted = 0
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED_FIELDS.issubset(reader.fieldnames):
            raise ValueError(f"CSV must include columns: {', '.join(sorted(REQUIRED_FIELDS))}")
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for row in reader:
                    views = int(row["views"])
                    ctr = float(row["click_through_rate"])
                    watch = float(row["watch_time_minutes"])
                    subscribers = int(row["subscribers_gained"])
                    if views < 0 or watch < 0 or subscribers < -views or not 0 <= ctr <= 1:
                        raise ValueError("Analytics values are outside accepted ranges (CTR is 0-1).")
                    conn.execute(
                        """INSERT INTO analytics
                           (job_id, imported_at, views, click_through_rate,
                            watch_time_minutes, subscribers_gained, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            row.get("job_id") or None, datetime.now(timezone.utc).isoformat(),
                            views, ctr, watch, subscribers, "Owner-imported; descriptive, not causal.",
                        ),
                    )
                    inserted += 1
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    return inserted


def report(db: Database) -> dict[str, object]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT COUNT(*) AS samples, SUM(views) AS views,
                      AVG(click_through_rate) AS average_ctr,
                      SUM(watch_time_minutes) AS watch_time_minutes,
                      SUM(subscribers_gained) AS subscribers_gained
               FROM analytics"""
        ).fetchone()
    return {
        "samples": rows["samples"],
        "views": rows["views"] or 0,
        "average_click_through_rate": rows["average_ctr"],
        "watch_time_minutes": rows["watch_time_minutes"] or 0,
        "subscribers_gained": rows["subscribers_gained"] or 0,
        "interpretation": "Descriptive observations only; these totals do not establish causation.",
    }
