"""Background jobs (the ``worker`` service runs these on a schedule).

* process_lola_queue  - Lola's queue -> isolated extractor -> structured extract for Charlie
* extract_transcripts - raw call transcripts -> extracts (kept)
* purge_transcripts   - delete raw transcripts older than the retention (7 days)
* export_audit        - append new audit entries to WORM object storage
* check_heartbeats    - alert when Charlie or Lola go quiet
* detect_anomalies    - audit-log rules -> kill switch PAUSE/STOP
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from assistants.agents.extractor import ExtractorLike
from assistants.audit import AuditEntry, AuditLog
from assistants.common.clock import Clock
from assistants.common.config import AnomalyRule
from assistants.lola_queue.store import QueueReader
from assistants.storage.transcripts import TranscriptStore


def process_lola_queue(
    queue: QueueReader,
    extractor: ExtractorLike,
    transcripts: TranscriptStore,
    clock: Clock,
    audit: AuditLog,
    limit: int = 20,
) -> int:
    n = 0
    for item in queue.unprocessed(limit):
        ext = extractor.extract(
            "lola_queue", item.untrusted_text, channel=item.channel, now=clock.now().isoformat()
        )
        data = ext.model_dump()
        data["channel"] = item.channel
        data["kind"] = item.kind
        transcripts.add_extract(str(item.id), "lola_queue", data, clock.now())
        queue.mark_processed(item.id, clock.now())
        audit.append("worker", "lola_queue.processed", {"item": str(item.id), "flags": ext.flags})
        n += 1
    return n


def purge_transcripts(transcripts: TranscriptStore, clock: Clock, audit: AuditLog) -> int:
    n = transcripts.purge_expired(clock.now())
    audit.append("worker", "transcripts.purged", {"count": n})
    return n


@dataclass
class Heartbeats:
    stale_after_s: int
    last: dict[str, datetime] = field(default_factory=dict)

    def beat(self, service: str, now: datetime) -> None:
        self.last[service] = now

    def stale(
        self, now: datetime, required: tuple[str, ...] = ("agents-charlie", "agents-lola")
    ) -> list[str]:
        limit = timedelta(seconds=self.stale_after_s)
        return [s for s in required if s not in self.last or now - self.last[s] > limit]


def check_heartbeats(hb: Heartbeats, clock: Clock, alert: Callable[[str], None]) -> list[str]:
    stale = hb.stale(clock.now())
    for service in stale:
        alert(f"{service} har ikke gitt livstegn på over {hb.stale_after_s} sekunder.")
    return stale


def _matches(rule: AnomalyRule, entry: AuditEntry) -> bool:
    match rule.kind:
        case "denied_burst":
            return entry.kind == "action.denied"
        case "level4_attempt":
            return entry.kind == "action.denied" and (
                entry.data.get("reason") == "level4_never"
                or (
                    str(entry.data.get("reason", "")).startswith("invalid")
                    and entry.data.get("level") == 4
                )
            )
        case "pin_failures":
            return entry.kind == "auth.pin_failed"
        case "cost_spike":
            return entry.kind == "llm.call"
        case "after_hours":
            return False


def detect_anomalies(
    audit: AuditLog,
    rules: list[AnomalyRule],
    clock: Clock,
    trigger: Callable[[str, str], object],
    since_seq: int = 0,
) -> list[str]:
    """Count matching audit entries in each rule's window; fire the kill switch on breach."""
    now = clock.now()
    entries = audit.entries(since_seq)
    fired = []
    for rule in rules:
        window = timedelta(seconds=rule.window_s)
        hits = [e for e in entries if now - e.ts <= window and _matches(rule, e)]
        if len(hits) >= rule.threshold:
            trigger(rule.name, rule.action)
            fired.append(rule.name)
    return fired
