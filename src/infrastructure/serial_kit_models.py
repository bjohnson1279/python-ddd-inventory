import uuid
from sqlalchemy import Column, String, Integer, BigInteger, JSON, ForeignKey, UniqueConstraint, Boolean
from src.infrastructure.database import Base


class TrackingModel(Base):
    __tablename__ = 'python_serial_tracking'
    __table_args__ = (UniqueConstraint('tenant_id', 'sku'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)


class SerialModel(Base):
    __tablename__ = 'python_serials'
    __table_args__ = (UniqueConstraint('tenant_id', 'serial_number'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    serial_number = Column(String, nullable=False)
    variant_id = Column(String, ForeignKey('products.id'), nullable=False)
    status = Column(String, nullable=False, default='pending')
    location_id = Column(String, nullable=False)
    destination_id = Column(String, nullable=True)
    layer_id = Column(String, ForeignKey('python_cost_layers.id'), nullable=True)
    cost_cents = Column(BigInteger, nullable=True)
    reservation = Column(String, nullable=True)
    version = Column(Integer, nullable=False, default=0)
    history = Column(JSON, nullable=False, default=list)


class KitModel(Base):
    __tablename__ = 'python_kits'
    __table_args__ = (UniqueConstraint('tenant_id', 'sku'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    name = Column(String, nullable=False)
    components = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=0)


class KitBuildModel(Base):
    __tablename__ = 'python_kit_builds'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    kit_id = Column(String, ForeignKey('python_kits.id'), nullable=False)
    layer_id = Column(String, ForeignKey('python_cost_layers.id'), nullable=False, unique=True)
    components = Column(JSON, nullable=False)
    disassembled = Column(Boolean, nullable=False, default=False)
