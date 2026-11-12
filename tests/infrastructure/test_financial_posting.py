from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import uuid
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

# Import models so all ForeignKey references are known to Base.metadata
import src.presentation.main
from src.infrastructure.database import Base
from src.infrastructure.financial_models import LedgerStateModel as State, JournalModel as Journal, JournalLineModel as Line, FinancialSourceModel as Source
from src.infrastructure.stock_models import StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.auth.identity import TenantModel
from src.infrastructure.financial_posting import (
    database_now,
    ensure_state,
    post_journal,
    capture_inventory_posting,
)


@pytest.fixture
def sync_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(TenantModel.__table__.insert().values(
            id="tenant-test",
            name="Test Store",
        ))
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_database_now_sqlite_and_postgres(sync_db):
    with sync_db.connect() as conn:
        now = database_now(conn)
        assert isinstance(now, datetime)
        assert now.tzinfo is not None

        # Test postgresql dialect branch
        mock_conn = MagicMock()
        mock_conn.dialect.name = "postgresql"
        fixed_dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_conn.scalar.return_value = fixed_dt

        res = database_now(mock_conn)
        assert res == fixed_dt
        mock_conn.scalar.assert_called_once()


def test_ensure_state_and_dialect_branches(sync_db):
    tenant = "tenant-test"
    with sync_db.begin() as conn:
        state = ensure_state(conn, tenant)
        assert state["tenant_id"] == tenant
        assert state["active"] is False
        assert state["last_number"] == 0
        assert state["version"] == 0

        # Idempotent call on existing tenant state
        state_again = ensure_state(conn, tenant)
        assert state_again["tenant_id"] == tenant

        # Test pg_insert branch
        mock_conn = MagicMock()
        mock_conn.dialect.name = "postgresql"
        mock_conn.execute.return_value.mappings.return_value.one.return_value = {"tenant_id": "pg-tenant"}
        res = ensure_state(mock_conn, "pg-tenant")
        assert res == {"tenant_id": "pg-tenant"}
        assert mock_conn.execute.call_count == 2


def test_post_journal_validation_and_period_guards(sync_db):
    tenant = "tenant-test"
    lines = [
        {"accountCode": "1000", "type": "debit", "amountCents": 100},
        {"accountCode": "3000", "type": "credit", "amountCents": 100},
    ]

    with sync_db.begin() as conn:
        # 1. Error when not active
        with pytest.raises(ValueError, match="Activate financial accounting before posting journals"):
            post_journal(conn, tenant, date=datetime.now(timezone.utc).date(), description="Test", actor="user-1", lines=lines)

        # Activate state
        now = database_now(conn)
        conn.execute(State.__table__.update().where(State.tenant_id == tenant).values(
            active=True,
            activated_at=now,
            method="ACCRUAL",
        ))

        # 2. Error when date > now.date() (future date)
        future_date = (now + timedelta(days=2)).date()
        with pytest.raises(ValueError, match="Journal date is outside the open accounting period"):
            post_journal(conn, tenant, date=future_date, description="Future", actor="user-1", lines=lines)

        # 3. Error when date < activated_at.date()
        past_date = (now - timedelta(days=2)).date()
        with pytest.raises(ValueError, match="Journal date is outside the open accounting period"):
            post_journal(conn, tenant, date=past_date, description="Past", actor="user-1", lines=lines)

        # 4. Error when closed_through and date <= closed_through
        conn.execute(State.__table__.update().where(State.tenant_id == tenant).values(
            closed_through=now.date(),
        ))
        with pytest.raises(ValueError, match="Journal date is outside the open accounting period"):
            post_journal(conn, tenant, date=now.date(), description="Closed", actor="user-1", lines=lines)


def test_post_journal_success_and_outbox(sync_db):
    tenant = "tenant-test"
    lines = [
        {"accountCode": "1000", "type": "debit", "amountCents": 500, "memo": "Cash debit"},
        {"accountCode": "3000", "type": "credit", "amountCents": 500},
    ]

    with sync_db.begin() as conn:
        now = database_now(conn)
        conn.execute(State.__table__.insert().values(
            tenant_id=tenant,
            active=True,
            activated_at=now,
            method="ACCRUAL",
            last_number=0,
            version=0,
        ))

        with patch("src.infrastructure.integration_models.enqueue_deliveries") as mock_enqueue:
            journal_id = post_journal(
                conn,
                tenant,
                date=now.date(),
                description="Initial Cash Opening",
                actor="actor-123",
                lines=lines,
                kind="MANUAL",
                reference="ref-1",
            )
            assert journal_id is not None
            mock_enqueue.assert_called_once()

        # Check journal entry
        journal = conn.execute(select(Journal.__table__).where(Journal.id == journal_id)).mappings().one()
        assert journal["number"] == 1
        assert journal["description"] == "Initial Cash Opening"
        assert journal["total_debits"] == 500
        assert journal["total_credits"] == 500
        assert journal["line_count"] == 2
        assert journal["actor_id"] == "actor-123"
        assert journal["reference_id"] == "ref-1"

        # Check lines
        db_lines = list(conn.execute(select(Line.__table__).where(Line.journal_id == journal_id).order_by(Line.position)).mappings())
        assert len(db_lines) == 2
        assert db_lines[0]["account_code"] == "1000"
        assert db_lines[0]["amount_cents"] == 500
        assert db_lines[0]["side"] == "debit"
        assert db_lines[0]["memo"] == "Cash debit"
        assert db_lines[1]["account_code"] == "3000"
        assert db_lines[1]["amount_cents"] == 500
        assert db_lines[1]["side"] == "credit"
        assert db_lines[1]["memo"] == ""

        # Check outbox event
        outbox = conn.execute(select(OutboxEventModel.__table__).where(OutboxEventModel.aggregate_id == journal_id)).mappings().one()
        assert outbox["event_type"] == "JournalEntryPosted"
        assert outbox["status"] == "PENDING"
        assert outbox["payload"]["journalId"] == journal_id
        assert outbox["payload"]["number"] == 1


def test_capture_inventory_posting_pre_activation(sync_db):
    tenant = "tenant-test"
    with Session(sync_db) as session:
        movement = StockMovementModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            operation="receive",
            amount=5,
            sku="SKU-1",
            location_id="loc-1",
            mutation_id="mut-1",
            actor_id="actor-1",
            request={"sku": "SKU-1", "quantity": 5},
            result={"receiptValueCents": 500},
        )
        session.add(movement)
        session.commit()

        # Check FinancialSource created with status PRE_ACTIVATION
        source = session.execute(select(Source.__table__).where(Source.movement_id == movement.id)).mappings().one()
        assert source["status"] == "PRE_ACTIVATION"
        assert source["journal_id"] is None
        assert source["amount_cents"] == 500


def test_capture_inventory_posting_unpriced_and_fallback_cost_layer(sync_db):
    tenant = "tenant-test"
    with Session(sync_db) as session:
        now = datetime.now(timezone.utc)
        session.execute(State.__table__.insert().values(
            tenant_id=tenant,
            active=True,
            activated_at=now,
            method="ACCRUAL",
            last_number=0,
            version=0,
        ))
        session.commit()

        # 1. Test fallback cost layers lookup for receive when receiptValueCents is None
        layer_id_1 = str(uuid.uuid4())
        layer_id_2 = str(uuid.uuid4())
        session.execute(CostLayerModel.__table__.insert(), [
            {"id": layer_id_1, "tenant_id": tenant, "stock_id": "stock-1", "sku": "SKU-2", "location_id": "loc-1", "initial_quantity": 2, "remaining_quantity": 2, "unit_cost_cents": 100, "initial_value_cents": 200, "remaining_value_cents": 200, "received_at": now},
            {"id": layer_id_2, "tenant_id": tenant, "stock_id": "stock-1", "sku": "SKU-2", "location_id": "loc-1", "initial_quantity": 3, "remaining_quantity": 3, "unit_cost_cents": 100, "initial_value_cents": 300, "remaining_value_cents": 300, "received_at": now},
        ])
        session.commit()

        mov_fallback = StockMovementModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            operation="receive",
            amount=5,
            sku="SKU-2",
            location_id="loc-1",
            mutation_id="mut-2",
            actor_id="actor-1",
            request={"sku": "SKU-2", "quantity": 5},
            result={"costLayerIds": [layer_id_1, layer_id_2]},
        )
        session.add(mov_fallback)
        session.commit()

        source_fallback = session.execute(select(Source.__table__).where(Source.movement_id == mov_fallback.id)).mappings().one()
        assert source_fallback["status"] == "POSTED"
        assert source_fallback["amount_cents"] == 500
        assert source_fallback["journal_id"] is not None

        # 2. Test unknown/unpriced movement facts -> status UNPRICED
        mov_unpriced = StockMovementModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            operation="receive",
            amount=2,
            sku="SKU-2",
            location_id="loc-1",
            mutation_id="mut-3",
            actor_id="actor-1",
            request={"sku": "SKU-2", "quantity": 2},
            result={},  # No receiptValueCents and no costLayerIds
        )
        session.add(mov_unpriced)
        session.commit()

        source_unpriced = session.execute(select(Source.__table__).where(Source.movement_id == mov_unpriced.id)).mappings().one()
        assert source_unpriced["status"] == "UNPRICED"
        assert source_unpriced["amount_cents"] is None
        assert source_unpriced["journal_id"] is None


def test_capture_inventory_posting_no_effect_and_serial_return_linking(sync_db):
    tenant = "tenant-test"
    with Session(sync_db) as session:
        now = datetime.now(timezone.utc)
        session.execute(State.__table__.insert().values(
            tenant_id=tenant,
            active=True,
            activated_at=now,
            method="ACCRUAL",
            last_number=0,
            version=0,
        ))
        session.commit()

        # 1. Operation with NO_EFFECT (e.g., allocate does not change financial inventory valuation)
        mov_alloc = StockMovementModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            operation="allocate",
            amount=1,
            sku="SKU-1",
            location_id="loc-1",
            mutation_id="mut-alloc",
            actor_id="actor-1",
            request={},
            result={},
        )
        session.add(mov_alloc)
        session.commit()

        source_alloc = session.execute(select(Source.__table__).where(Source.movement_id == mov_alloc.id)).mappings().one()
        assert source_alloc["status"] == "NO_EFFECT"
        assert source_alloc["journal_id"] is None

        # 2. Test serial-return linking to prior SALE sourceMovementId
        sale_mov_id = str(uuid.uuid4())
        source_layer_id = "layer-serial-999"

        # Insert a prior SALE movement and its FinancialSource
        mov_sale = StockMovementModel(
            id=sale_mov_id,
            tenant_id=tenant,
            operation="dispatch",
            amount=1,
            sku="SERIAL-ITEM",
            location_id="loc-1",
            mutation_id="mut-sale",
            actor_id="actor-1",
            request={},
            result={"consumedLayers": [{"layerId": source_layer_id, "quantity": 1, "unitCostCents": 100}]},
        )
        session.add(mov_sale)
        session.commit()

        # Update the sale source's facts to contain sourceCostLayerId
        session.execute(Source.__table__.update().where(Source.movement_id == sale_mov_id).values(
            kind="SALE",
            facts={"result": {"sourceCostLayerId": source_layer_id}},
        ))
        session.commit()

        # Now insert serial-return movement
        mov_return = StockMovementModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            operation="serial-return",
            amount=1,
            sku="SERIAL-ITEM",
            location_id="loc-1",
            mutation_id="mut-return",
            actor_id="actor-1",
            request={},
            result={"sourceLayerId": source_layer_id, "costCents": 100},
        )
        session.add(mov_return)
        session.commit()

        source_return = session.execute(select(Source.__table__).where(Source.movement_id == mov_return.id)).mappings().one()
        assert source_return["facts"]["result"]["sourceMovementId"] == sale_mov_id
