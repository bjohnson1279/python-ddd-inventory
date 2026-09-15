import pytest
from datetime import datetime, timezone
import time
from src.domain.ledger import ComplianceLedger, LedgerEntry

def test_ledger_initialization():
    ledger = ComplianceLedger()
    assert len(ledger._entries) == 0
    assert ledger._latest_hash == "GENESIS_HASH"

def test_append_event():
    ledger = ComplianceLedger()
    payload = {"quantity": 10, "item": "widget"}

    entry = ledger.append_event(
        aggregate_type="Inventory",
        aggregate_id="inv-123",
        event_type="ItemAdded",
        payload=payload
    )

    assert len(ledger._entries) == 1
    assert ledger._entries[0] == entry
    assert entry.previous_hash == "GENESIS_HASH"
    assert entry.hash != "GENESIS_HASH"
    assert entry.hash == entry.compute_hash()
    assert ledger._latest_hash == entry.hash

def test_get_events_for_aggregate():
    ledger = ComplianceLedger()

    ledger.append_event("Inventory", "inv-1", "ItemAdded", {})
    ledger.append_event("Inventory", "inv-2", "ItemAdded", {})
    ledger.append_event("Inventory", "inv-1", "ItemRemoved", {})

    events_inv1 = ledger.get_events_for_aggregate("inv-1")
    assert len(events_inv1) == 2
    assert all(e.aggregate_id == "inv-1" for e in events_inv1)

    events_inv2 = ledger.get_events_for_aggregate("inv-2")
    assert len(events_inv2) == 1
    assert all(e.aggregate_id == "inv-2" for e in events_inv2)

def test_reconstruct_state_at():
    ledger = ComplianceLedger()

    e1 = ledger.append_event("Inventory", "inv-1", "Created", {})
    time.sleep(0.01) # to ensure different timestamps
    point_in_time = datetime.now(timezone.utc)
    time.sleep(0.01)
    e2 = ledger.append_event("Inventory", "inv-1", "Updated", {})

    reconstructed = ledger.reconstruct_state_at("inv-1", point_in_time)

    assert len(reconstructed) == 1
    assert reconstructed[0].entry_id == e1.entry_id

    # After e2 point in time
    point_in_time_after = datetime.now(timezone.utc)
    reconstructed_all = ledger.reconstruct_state_at("inv-1", point_in_time_after)
    assert len(reconstructed_all) == 2

def test_verify_ledger_integrity_success():
    ledger = ComplianceLedger()

    ledger.append_event("Inventory", "inv-1", "Created", {})
    ledger.append_event("Inventory", "inv-1", "Updated", {})
    ledger.append_event("Inventory", "inv-2", "Created", {})

    assert ledger.verify_ledger_integrity() is True

def test_verify_ledger_integrity_tampered_payload():
    ledger = ComplianceLedger()

    ledger.append_event("Inventory", "inv-1", "Created", {"val": 1})
    ledger.append_event("Inventory", "inv-1", "Updated", {"val": 2})

    # Tamper with the first entry's payload
    ledger._entries[0].payload = {"val": 999}

    # Hash doesn't match compute_hash anymore
    assert ledger.verify_ledger_integrity() is False

def test_verify_ledger_integrity_tampered_hash():
    ledger = ComplianceLedger()

    ledger.append_event("Inventory", "inv-1", "Created", {})
    ledger.append_event("Inventory", "inv-1", "Updated", {})

    # Alter hash directly
    ledger._entries[0].hash = "FAKE_HASH"

    assert ledger.verify_ledger_integrity() is False

def test_verify_ledger_integrity_tampered_previous_hash():
    ledger = ComplianceLedger()

    ledger.append_event("Inventory", "inv-1", "Created", {})
    ledger.append_event("Inventory", "inv-1", "Updated", {})

    # Alter previous_hash directly on the second entry
    ledger._entries[1].previous_hash = "FAKE_PREV_HASH"

    # Doesn't match current_hash (which is entries[0].hash)
    assert ledger.verify_ledger_integrity() is False
