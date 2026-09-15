from dataclasses import dataclass, field
from typing import List
from .events import DomainEvent

@dataclass
class AggregateRoot:
    _domain_events: List[DomainEvent] = field(default_factory=list, init=False, repr=False)

    def add_domain_event(self, event: DomainEvent):
        self._domain_events.append(event)

    def clear_domain_events(self):
        self._domain_events.clear()

    @property
    def domain_events(self) -> List[DomainEvent]:
        return list(self._domain_events)
