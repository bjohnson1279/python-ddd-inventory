from sqlalchemy import Column, String, DateTime, JSON
from src.infrastructure.database import Base
from datetime import datetime
import uuid

class OutboxEventModel(Base):
    __tablename__ = 'outbox_events'

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    aggregate_type = Column(String, nullable=False)
    aggregate_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
