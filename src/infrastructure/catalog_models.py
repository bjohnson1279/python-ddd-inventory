import uuid
from sqlalchemy import Column, String, ForeignKey, ForeignKeyConstraint, JSON, Boolean, UniqueConstraint, DateTime, Index
from datetime import datetime, timezone
from src.infrastructure.database import Base


class CatalogModel(Base):
    __tablename__ = 'python_catalog'
    tenant_id = Column(String, primary_key=True)
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)


class VariantLinkModel(Base):
    __tablename__ = 'python_variant_links'
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'product_id'],
                                          ['python_catalog.tenant_id', 'python_catalog.id']),)
    tenant_id = Column(String, primary_key=True)
    sku = Column(String, primary_key=True)
    product_id = Column(String, nullable=False)
    variant_id = Column(String, ForeignKey('products.id'), nullable=False, unique=True)
    tracking_mode = Column(String, nullable=False, default='quantity')
    attributes = Column(JSON, nullable=False, default=list)


class BarcodeModel(Base):
    __tablename__ = 'python_barcodes'
    __table_args__ = (UniqueConstraint('tenant_id', 'value'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    value = Column(String, nullable=False)
    symbology = Column(String, nullable=False)
    source = Column(String, nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    assigned_at = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))


Index('python_barcodes_one_primary_per_sku', BarcodeModel.tenant_id, BarcodeModel.sku,
      unique=True, postgresql_where=BarcodeModel.is_primary.is_(True),
      sqlite_where=BarcodeModel.is_primary.is_(True))
