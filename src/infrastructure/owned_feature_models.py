import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
from src.infrastructure.database import Base


class ReportDefinitionModel(Base):
    __tablename__ = 'python_report_definitions'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    created_by = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)


class NotificationModel(Base):
    __tablename__ = 'python_notifications'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, ForeignKey('python_users.id'), nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, nullable=False, default='UNREAD')
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
