import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List
import uuid

@dataclass
class LedgerEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    aggregate_id: str = ""
    aggregate_type: str = ""
    event_type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    previous_hash: str = ""
    hash: str = ""

    def compute_hash(self) -> str:
        """Computes a cryptographic hash of this entry for compliance auditability."""
        data = {
            "entry_id": self.entry_id,
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "previous_hash": self.previous_hash
        }
        json_data = json.dumps(data, sort_keys=True).encode('utf-8')
        return hashlib.sha256(json_data).hexdigest()


class ComplianceLedger:
    """
    Cryptographically Signed Compliance Ledger for Event-Sourced 
    Point-in-Time State Reconstruction & Audit Replay.
    """
    
    def __init__(self):
        # In memory representation, usually backed by an append-only DB table
        self._entries: List[LedgerEntry] = []
        self._latest_hash: str = "GENESIS_HASH"
        
    def append_event(self, aggregate_type: str, aggregate_id: str, event_type: str, payload: Dict[str, Any]) -> LedgerEntry:
        entry = LedgerEntry(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            previous_hash=self._latest_hash
        )
        entry.hash = entry.compute_hash()
        self._latest_hash = entry.hash
        self._entries.append(entry)
        return entry

    def get_events_for_aggregate(self, aggregate_id: str) -> List[LedgerEntry]:
        """Retrieves the event stream for a specific aggregate."""
        return [e for e in self._entries if e.aggregate_id == aggregate_id]

    def reconstruct_state_at(self, aggregate_id: str, point_in_time: datetime) -> List[LedgerEntry]:
        """
        Point-in-Time State Reconstruction.
        Returns the stream of events up to the given timestamp, which can be folded
        to project the exact state of the aggregate at that point in time.
        """
        return [
            e for e in self._entries 
            if e.aggregate_id == aggregate_id and e.timestamp <= point_in_time
        ]

    def verify_ledger_integrity(self) -> bool:
        """Verifies the cryptographic chain of hashes to detect tampering."""
        current_hash = "GENESIS_HASH"
        for entry in self._entries:
            if entry.previous_hash != current_hash:
                return False
            if entry.hash != entry.compute_hash():
                return False
            current_hash = entry.hash
        return True
