import pytest
from datetime import datetime, timezone
import uuid
from dataclasses import dataclass

from src.domain.events import DomainEvent

def test_domain_event_initialization():
    event = DomainEvent()

    # Verify event_id is a valid UUID string
    assert isinstance(event.event_id, str)
    assert uuid.UUID(event.event_id)

    # Verify occurred_on is a datetime object in UTC
    assert isinstance(event.occurred_on, datetime)
    assert event.occurred_on.tzinfo == timezone.utc

def test_domain_event_name_property():
    event = DomainEvent()
    assert event.event_name == "DomainEvent"

    @dataclass
    class CustomEvent(DomainEvent):
        pass

    custom_event = CustomEvent()
    assert custom_event.event_name == "CustomEvent"

def test_domain_event_to_dict_base():
    event = DomainEvent()
    event_dict = event.to_dict()

    assert isinstance(event_dict, dict)
    assert event_dict["event_id"] == event.event_id
    assert event_dict["event_name"] == "DomainEvent"
    assert event_dict["occurred_on"] == event.occurred_on.isoformat()
    assert len(event_dict) == 3

def test_domain_event_to_dict_with_custom_attributes():
    @dataclass
    class ProductCreated(DomainEvent):
        product_id: str = "prod-123"
        price: float = 99.99
        quantity: int = 10

    event = ProductCreated()
    event_dict = event.to_dict()

    assert event_dict["event_id"] == event.event_id
    assert event_dict["event_name"] == "ProductCreated"
    assert event_dict["occurred_on"] == event.occurred_on.isoformat()

    # Verify custom attributes are included
    assert event_dict["product_id"] == "prod-123"
    assert event_dict["price"] == 99.99
    assert event_dict["quantity"] == 10

    # Verify total number of keys (3 base + 3 custom)
    assert len(event_dict) == 6

def test_domain_event_custom_init():
    custom_id = "custom-id-123"
    custom_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    event = DomainEvent(event_id=custom_id, occurred_on=custom_time)

    assert event.event_id == custom_id
    assert event.occurred_on == custom_time

    event_dict = event.to_dict()
    assert event_dict["event_id"] == custom_id
    assert event_dict["occurred_on"] == custom_time.isoformat()
