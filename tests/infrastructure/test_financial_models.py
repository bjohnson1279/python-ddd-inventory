import json
import uuid
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock
import pytest
import pytest_asyncio
from sqlalchemy import text, select, update, delete
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.infrastructure.database import Base
from src.infrastructure.auth.identity import TenantModel, UserModel
from src.infrastructure.product.models import ProductModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.procurement_models import SupplierModel
from src.infrastructure.financial_models import (
    LedgerStateModel,
    JournalModel,
    JournalLineModel,
    FinancialSourceModel,
    FinancialDocumentModel,
    DocumentSourceModel,
    SettlementModel,
    FinancialCreditModel,
    install_journal_guards,
    install_line_guards,
)


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Seed parent records for foreign keys
        await conn.execute(
            text(
                "INSERT INTO python_tenants (id, name) VALUES ('tenant-1', 'Test Tenant')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO python_users (id, tenant_id, email, password_hash, role, name, is_active, auth_version) "
                "VALUES ('user-1', 'tenant-1', 'user@test.com', 'hash', 'ADMIN', 'User 1', 1, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO python_suppliers (id, tenant_id, name, email, lead_time_days, is_active, version) "
                "VALUES ('sup-1', 'tenant-1', 'Supplier 1', 'sup@test.com', 0, 1, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO python_stock (id, tenant_id, sku, location_id, quantity, allocated, version) "
                "VALUES ('stk-1', 'tenant-1', 'SKU-1', 'loc-1', 100, 0, 1)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO python_stock_movements (id, tenant_id, mutation_id, actor_id, sku, location_id, operation, amount, request, result, occurred_at) "
                "VALUES ('mov-1', 'tenant-1', 'mut-1', 'user-1', 'SKU-1', 'loc-1', 'RECEIVE', 10, '{}', '{}', CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO python_stock_movements (id, tenant_id, mutation_id, actor_id, sku, location_id, operation, amount, request, result, occurred_at) "
                "VALUES ('mov-2', 'tenant-1', 'mut-2', 'user-1', 'SKU-1', 'loc-1', 'RECEIVE', 5, '{}', '{}', CURRENT_TIMESTAMP)"
            )
        )

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_ledger_state_model_crud(async_db):
    now = datetime.now(timezone.utc)
    today = now.date()
    async with async_db() as session:
        # CheckConstraints: active=False allows null method/activated_at
        state = LedgerStateModel(
            tenant_id="tenant-1",
            active=False,
            last_number=0,
            version=0,
        )
        session.add(state)
        await session.commit()

        # Query
        stmt = select(LedgerStateModel).where(LedgerStateModel.tenant_id == "tenant-1")
        result = (await session.execute(stmt)).scalar_one()
        assert result.tenant_id == "tenant-1"
        assert result.active is False
        assert result.version == 0
        assert result.last_number == 0

        # Update to active with required fields
        result.active = True
        result.method = "ACCRUAL"
        result.activated_at = now
        result.closed_through = today - timedelta(days=1)
        result.last_number = 5
        result.version = 1
        await session.commit()

        # Verify update
        updated = (await session.execute(stmt)).scalar_one()
        assert updated.active is True
        assert updated.method == "ACCRUAL"
        assert updated.last_number == 5


@pytest.mark.asyncio
async def test_ledger_state_model_constraints(async_db):
    # Constraint: active=True without method or activated_at fails
    async with async_db() as session:
        state_invalid = LedgerStateModel(
            tenant_id="tenant-1",
            active=True,
            method=None,
            activated_at=None,
        )
        session.add(state_invalid)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_financial_source_model_crud(async_db):
    today = date.today()
    async with async_db() as session:
        source = FinancialSourceModel(
            movement_id="mov-1",
            tenant_id="tenant-1",
            date=today,
            kind="RECEIPT",
            status="PENDING",
            facts={"sku": "SKU-1", "qty": 10},
            amount_cents=5000,
            version=0,
        )
        session.add(source)
        await session.commit()

        stmt = select(FinancialSourceModel).where(FinancialSourceModel.movement_id == "mov-1")
        result = (await session.execute(stmt)).scalar_one()
        assert result.movement_id == "mov-1"
        assert result.amount_cents == 5000
        assert result.status == "PENDING"
        assert result.facts == {"sku": "SKU-1", "qty": 10}


@pytest.mark.asyncio
async def test_financial_document_model_crud_and_constraints(async_db):
    today = date.today()
    doc_id = str(uuid.uuid4())
    async with async_db() as session:
        doc = FinancialDocumentModel(
            id=doc_id,
            tenant_id="tenant-1",
            kind="INVOICE",
            reference="INV-001",
            counterparty="Vendor Corp",
            supplier_id="sup-1",
            date=today,
            method="ACCRUAL",
            amount_cents=10000,
            settled_cents=5000,
            credited_cents=1000,
            refunded_cents=500,
            version=0,
        )
        session.add(doc)
        await session.commit()

        stmt = select(FinancialDocumentModel).where(FinancialDocumentModel.id == doc_id)
        fetched = (await session.execute(stmt)).scalar_one()
        assert fetched.reference == "INV-001"
        assert fetched.settled_cents == 5000
        assert fetched.counterparty == "Vendor Corp"

        # Unique constraint: tenant_id, kind, reference
        duplicate_doc = FinancialDocumentModel(
            tenant_id="tenant-1",
            kind="INVOICE",
            reference="INV-001",
            counterparty="Other",
            date=today,
            method="ACCRUAL",
            amount_cents=1000,
        )
        session.add(duplicate_doc)
        with pytest.raises(IntegrityError):
            await session.commit()

    # Check constraint: amount_cents > 0
    async with async_db() as session:
        invalid_doc = FinancialDocumentModel(
            tenant_id="tenant-1",
            kind="BILL",
            reference="BILL-001",
            counterparty="Vendor",
            date=today,
            method="ACCRUAL",
            amount_cents=0,  # Invalid: amount_cents must be > 0
        )
        session.add(invalid_doc)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_document_source_model(async_db):
    today = date.today()
    doc_id = str(uuid.uuid4())
    async with async_db() as session:
        # Set up financial source and document first
        source = FinancialSourceModel(
            movement_id="mov-1",
            tenant_id="tenant-1",
            date=today,
            kind="RECEIPT",
            status="PENDING",
            facts={},
            amount_cents=1000,
        )
        doc = FinancialDocumentModel(
            id=doc_id,
            tenant_id="tenant-1",
            kind="BILL",
            reference="BILL-DOC-1",
            counterparty="Supplier",
            date=today,
            method="ACCRUAL",
            amount_cents=1000,
        )
        session.add(source)
        session.add(doc)
        await session.commit()

        # Link document and source
        doc_source = DocumentSourceModel(
            source_id="mov-1",
            tenant_id="tenant-1",
            document_id=doc_id,
        )
        session.add(doc_source)
        await session.commit()

        stmt = select(DocumentSourceModel).where(DocumentSourceModel.source_id == "mov-1")
        link = (await session.execute(stmt)).scalar_one()
        assert link.document_id == doc_id
        assert link.tenant_id == "tenant-1"


@pytest.mark.asyncio
async def test_journal_and_line_models_with_sqlite_triggers(async_db):
    now = datetime.now(timezone.utc)
    today = now.date()
    journal_id = str(uuid.uuid4())
    line1_id = str(uuid.uuid4())
    line2_id = str(uuid.uuid4())

    lines_json = [
        {
            "id": line1_id,
            "accountCode": "1000",
            "amountCents": 500,
            "type": "debit",
            "memo": "Debit cash",
        },
        {
            "id": line2_id,
            "accountCode": "2000",
            "amountCents": 500,
            "type": "credit",
            "memo": "Credit AP",
        },
    ]

    async with async_db() as session:
        # First, active ledger state is required by the trigger python_journal_validate
        state = LedgerStateModel(
            tenant_id="tenant-1",
            active=True,
            method="ACCRUAL",
            activated_at=now - timedelta(days=10),
            closed_through=today - timedelta(days=2),
            last_number=0,
            version=0,
        )
        session.add(state)
        await session.commit()

        # Insert a valid journal
        journal = JournalModel(
            id=journal_id,
            tenant_id="tenant-1",
            number=1,
            date=today,
            posted_at=now,
            description="Test Opening Journal",
            actor_id="user-1",
            method="ACCRUAL",
            kind="MANUAL",
            lines=lines_json,
            line_count=2,
            total_debits=500,
            total_credits=500,
        )
        session.add(journal)
        await session.commit()

        # Insert valid matching journal lines
        line1 = JournalLineModel(
            id=line1_id,
            tenant_id="tenant-1",
            journal_id=journal_id,
            position=0,
            account_code="1000",
            side="debit",
            amount_cents=500,
            memo="Debit cash",
        )
        line2 = JournalLineModel(
            id=line2_id,
            tenant_id="tenant-1",
            journal_id=journal_id,
            position=1,
            account_code="2000",
            side="credit",
            amount_cents=500,
            memo="Credit AP",
        )
        session.add_all([line1, line2])
        await session.commit()

        # Verify inserted lines
        lines_stmt = select(JournalLineModel).where(JournalLineModel.journal_id == journal_id)
        saved_lines = (await session.execute(lines_stmt)).scalars().all()
        assert len(saved_lines) == 2


@pytest.mark.asyncio
async def test_journal_triggers_prevent_immutability_violation(async_db):
    now = datetime.now(timezone.utc)
    today = now.date()
    journal_id = str(uuid.uuid4())
    line1_id = str(uuid.uuid4())
    line2_id = str(uuid.uuid4())

    lines_json = [
        {"id": line1_id, "accountCode": "1000", "amountCents": 500, "type": "debit", "memo": "D"},
        {"id": line2_id, "accountCode": "2000", "amountCents": 500, "type": "credit", "memo": "C"},
    ]

    async with async_db() as session:
        state = LedgerStateModel(
            tenant_id="tenant-1",
            active=True,
            method="ACCRUAL",
            activated_at=now - timedelta(days=5),
            last_number=0,
        )
        session.add(state)
        await session.flush()
        journal = JournalModel(
            id=journal_id,
            tenant_id="tenant-1",
            number=1,
            date=today,
            posted_at=now,
            description="Original Journal",
            actor_id="user-1",
            method="ACCRUAL",
            kind="MANUAL",
            lines=lines_json,
            line_count=2,
            total_debits=500,
            total_credits=500,
        )
        session.add(journal)
        await session.commit()

        # Test UPDATE guard on journal
        with pytest.raises(DBAPIError, match="Posted journals are immutable"):
            await session.execute(
                update(JournalModel).where(JournalModel.id == journal_id).values(description="Modified")
            )
        await session.rollback()

        # Test DELETE guard on journal
        with pytest.raises(DBAPIError, match="Posted journals are immutable"):
            await session.execute(
                delete(JournalModel).where(JournalModel.id == journal_id)
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_journal_line_triggers_prevent_immutability_violation(async_db):
    now = datetime.now(timezone.utc)
    today = now.date()
    journal_id = str(uuid.uuid4())
    line1_id = str(uuid.uuid4())
    line2_id = str(uuid.uuid4())

    lines_json = [
        {"id": line1_id, "accountCode": "1000", "amountCents": 100, "type": "debit", "memo": "D"},
        {"id": line2_id, "accountCode": "1000", "amountCents": 100, "type": "credit", "memo": "C"},
    ]

    async with async_db() as session:
        state = LedgerStateModel(
            tenant_id="tenant-1",
            active=True,
            method="ACCRUAL",
            activated_at=now - timedelta(days=5),
            last_number=0,
        )
        session.add(state)
        await session.flush()
        journal = JournalModel(
            id=journal_id,
            tenant_id="tenant-1",
            number=1,
            date=today,
            posted_at=now,
            description="Line test",
            actor_id="user-1",
            method="ACCRUAL",
            kind="MANUAL",
            lines=lines_json,
            line_count=2,
            total_debits=100,
            total_credits=100,
        )
        session.add(journal)
        await session.flush()
        line = JournalLineModel(
            id=line1_id,
            tenant_id="tenant-1",
            journal_id=journal_id,
            position=0,
            account_code="1000",
            side="debit",
            amount_cents=100,
            memo="D",
        )
        session.add(line)
        await session.commit()

        # Test UPDATE on journal line
        with pytest.raises(DBAPIError, match="Posted lines are immutable"):
            await session.execute(
                update(JournalLineModel).where(JournalLineModel.id == line1_id).values(memo="Tampered")
            )
        await session.rollback()

        # Test DELETE on journal line
        with pytest.raises(DBAPIError, match="Posted lines are immutable"):
            await session.execute(
                delete(JournalLineModel).where(JournalLineModel.id == line1_id)
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_journal_trigger_validations(async_db):
    now = datetime.now(timezone.utc)
    today = now.date()
    line1_id = str(uuid.uuid4())
    line2_id = str(uuid.uuid4())

    async with async_db() as session:
        state = LedgerStateModel(
            tenant_id="tenant-1",
            active=True,
            method="ACCRUAL",
            activated_at=now - timedelta(days=1),
            closed_through=today,  # today is closed!
            last_number=0,
        )
        session.add(state)
        await session.commit()

        # Journal on or before closed_through triggers 'Journal is outside the active open period'
        j1 = JournalModel(
            id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            number=1,
            date=today,
            posted_at=now,
            description="Closed date test",
            actor_id="user-1",
            method="ACCRUAL",
            kind="MANUAL",
            lines=[
                {"id": line1_id, "accountCode": "1000", "amountCents": 100, "type": "debit", "memo": "D"},
                {"id": line2_id, "accountCode": "1000", "amountCents": 100, "type": "credit", "memo": "C"},
            ],
            line_count=2,
            total_debits=100,
            total_credits=100,
        )
        session.add(j1)
        with pytest.raises(DBAPIError, match="Journal is outside the active open period"):
            await session.commit()
        await session.rollback()

    # Re-open the period
    async with async_db() as session:
        await session.execute(
            update(LedgerStateModel).where(LedgerStateModel.tenant_id == "tenant-1").values(closed_through=None)
        )
        await session.commit()

        # Trigger check: Unbalanced debit/credit sum vs total_debits/total_credits
        j2 = JournalModel(
            id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            number=1,
            date=today,
            posted_at=now,
            description="Unbalanced lines vs totals",
            actor_id="user-1",
            method="ACCRUAL",
            kind="MANUAL",
            lines=[
                {"id": line1_id, "accountCode": "1000", "amountCents": 200, "type": "debit", "memo": "D"},
                {"id": line2_id, "accountCode": "1000", "amountCents": 100, "type": "credit", "memo": "C"},
            ],
            line_count=2,
            total_debits=100,  # line says 200 debits
            total_credits=100,
        )
        session.add(j2)
        with pytest.raises(DBAPIError, match="Unbalanced or inconsistent journal"):
            await session.commit()
        await session.rollback()

        # Trigger check: line mismatch when inserting into python_journal_lines
        valid_journal = JournalModel(
            id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            number=1,
            date=today,
            posted_at=now,
            description="Valid journal",
            actor_id="user-1",
            method="ACCRUAL",
            kind="MANUAL",
            lines=[
                {"id": line1_id, "accountCode": "1000", "amountCents": 100, "type": "debit", "memo": "D"},
                {"id": line2_id, "accountCode": "1000", "amountCents": 100, "type": "credit", "memo": "C"},
            ],
            line_count=2,
            total_debits=100,
            total_credits=100,
        )
        session.add(valid_journal)
        await session.commit()

        # Insert a line with mismatching account_code
        mismatched_line = JournalLineModel(
            id=line1_id,
            tenant_id="tenant-1",
            journal_id=valid_journal.id,
            position=0,
            account_code="2000",  # Journal says 1000
            side="debit",
            amount_cents=100,
            memo="D",
        )
        session.add(mismatched_line)
        with pytest.raises(DBAPIError, match="Journal line does not match its owned journal"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_settlement_and_financial_credit_models(async_db):
    today = date.today()
    doc_id = str(uuid.uuid4())
    async with async_db() as session:
        # Create base source and document
        source = FinancialSourceModel(
            movement_id="mov-1",
            tenant_id="tenant-1",
            date=today,
            kind="RECEIPT",
            status="PENDING",
            facts={},
            amount_cents=5000,
        )
        doc = FinancialDocumentModel(
            id=doc_id,
            tenant_id="tenant-1",
            kind="INVOICE",
            reference="INV-SETTLE",
            counterparty="Customer",
            date=today,
            method="ACCRUAL",
            amount_cents=5000,
        )
        session.add_all([source, doc])
        await session.commit()

        # Create SettlementModel
        settlement_id = str(uuid.uuid4())
        settlement = SettlementModel(
            id=settlement_id,
            tenant_id="tenant-1",
            document_id=doc_id,
            reference="SETTLE-REF-1",
            date=today,
            amount_cents=2000,
            journal_id="j-fake-id",
            direction="PAYMENT",
        )
        session.add(settlement)
        await session.commit()

        # Verify settlement
        s = (
            await session.execute(
                select(SettlementModel).where(SettlementModel.id == settlement_id)
            )
        ).scalar_one()
        assert s.reference == "SETTLE-REF-1"
        assert s.amount_cents == 2000
        assert s.direction == "PAYMENT"

        # Unique reference constraint on settlement
        dup_settlement = SettlementModel(
            tenant_id="tenant-1",
            document_id=doc_id,
            reference="SETTLE-REF-1",
            date=today,
            amount_cents=1000,
            journal_id="j-fake-id",
        )
        session.add(dup_settlement)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # Check constraint on settlement: amount_cents > 0
        zero_settlement = SettlementModel(
            tenant_id="tenant-1",
            document_id=doc_id,
            reference="SETTLE-ZERO",
            date=today,
            amount_cents=0,
            journal_id="j-fake-id",
        )
        session.add(zero_settlement)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # Create FinancialCreditModel
        credit_id = str(uuid.uuid4())
        credit = FinancialCreditModel(
            id=credit_id,
            tenant_id="tenant-1",
            document_id=doc_id,
            source_id="mov-1",
            reference="CREDIT-REF-1",
            date=today,
            amount_cents=1500,
            journal_id="j-fake-id",
        )
        session.add(credit)
        await session.commit()

        # Verify credit
        c = (
            await session.execute(
                select(FinancialCreditModel).where(FinancialCreditModel.id == credit_id)
            )
        ).scalar_one()
        assert c.reference == "CREDIT-REF-1"
        assert c.amount_cents == 1500
        assert c.source_id == "mov-1"

        # Unique reference constraint on credit
        dup_credit = FinancialCreditModel(
            tenant_id="tenant-1",
            document_id=doc_id,
            source_id="mov-1",
            reference="CREDIT-REF-1",
            date=today,
            amount_cents=500,
        )
        session.add(dup_credit)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # Check constraint on credit: amount_cents > 0
        zero_credit = FinancialCreditModel(
            tenant_id="tenant-1",
            document_id=doc_id,
            source_id="mov-1",
            reference="CREDIT-ZERO",
            date=today,
            amount_cents=0,
        )
        session.add(zero_credit)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


def test_install_journal_guards_non_sqlite():
    """Verify PostgreSQL branch of install_journal_guards triggers DDL execution."""
    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"
    install_journal_guards(JournalModel.__table__, mock_conn)
    assert mock_conn.execute.call_count == 2
    executed_statements = [str(call[0][0]) for call in mock_conn.execute.call_args_list]
    assert any("CREATE OR REPLACE FUNCTION python_validate_journal()" in stmt for stmt in executed_statements)
    assert any("CREATE TRIGGER python_journal_guard" in stmt for stmt in executed_statements)


def test_install_line_guards_non_sqlite():
    """Verify PostgreSQL branch of install_line_guards triggers DDL execution."""
    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"
    install_line_guards(JournalLineModel.__table__, mock_conn)
    assert mock_conn.execute.call_count == 2
    executed_statements = [str(call[0][0]) for call in mock_conn.execute.call_args_list]
    assert any("CREATE OR REPLACE FUNCTION python_validate_journal_line()" in stmt for stmt in executed_statements)
    assert any("CREATE TRIGGER python_line_guard" in stmt for stmt in executed_statements)
