"""
Server-side incident history for AP-NET.

Before this existed the entire incident log lived in one browser's localStorage
(FIX_PLAN.md B2): opening the dashboard from another machine showed an empty history,
clearing site data destroyed the record permanently, and nothing on the server could be
audited after the fact. For an anti-poaching system whose whole purpose is producing a
defensible record of human intrusions, that was the most serious design gap in the project.

Storage is append-only JSONL at base_station/received_images/incidents.jsonl - one JSON
object per line, so a partially written tail can never corrupt earlier records.
"""

import os
import json
import uuid
import threading
from datetime import datetime, timezone

_lock = threading.Lock()


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_log_id():
    return f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def append(store_path, incident):
    """
    Append one incident. Returns the stored record (with log_id / stored_at filled in).
    """
    record = dict(incident)
    record.setdefault("log_id", new_log_id())
    record.setdefault("stored_at", _utcnow())

    with _lock:
        os.makedirs(os.path.dirname(store_path), exist_ok=True)
        with open(store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_all(store_path, limit=None):
    """
    Read every incident, oldest first. Malformed lines are skipped rather than
    aborting the load, so one bad write cannot make the whole history unreadable.
    """
    if not os.path.exists(store_path):
        return []

    records = []
    with _lock:
        with open(store_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"[STORE] Skipping malformed line {line_no} in {store_path}")

    return records[-limit:] if limit else records


def update_action(store_path, log_id, action):
    """
    Set the `action` field of one incident (e.g. PATROL_RESOLVED).

    Rewrites the file atomically via a temp file + os.replace, so an interrupted write
    leaves the original history intact. Returns the updated record, or None if not found.
    """
    if not os.path.exists(store_path):
        return None

    with _lock:
        with open(store_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = None
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                out.append(stripped)          # preserve unreadable lines verbatim
                continue

            if rec.get("log_id") == log_id:
                rec["action"] = action
                rec["resolved_at"] = _utcnow()
                updated = rec
            out.append(json.dumps(rec, ensure_ascii=False))

        if updated is None:
            return None

        tmp_path = store_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp_path, store_path)

    return updated


def latest(store_path):
    """Most recent incident, or None."""
    records = load_all(store_path, limit=1)
    return records[-1] if records else None


def stats(store_path):
    """Aggregate counters for the dashboard's statistics panel."""
    records = load_all(store_path)
    total = len(records)
    resolved = sum(1 for r in records if r.get("action") == "PATROL_RESOLVED")
    return {
        "total": total,
        "active": total - resolved,
        "resolved": resolved,
        "resolve_rate": round(resolved / total * 100) if total else 0,
    }
