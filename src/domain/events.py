from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Dict, Any

@dataclass
class DomainEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_on: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def event_name(self) -> str:
        return self.__class__.__name__

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "occurred_on": self.occurred_on.isoformat(),
            **{k: v for k, v in self.__dict__.items() if k not in ["event_id", "occurred_on"]}
        }
