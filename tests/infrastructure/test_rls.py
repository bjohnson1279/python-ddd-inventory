import pytest
from unittest.mock import AsyncMock, MagicMock
from src.infrastructure.auth.rls import install_tenant_policies, TENANT_TABLES, _build_table_predicate


def test_tenant_tables_count():
    assert len(TENANT_TABLES) == 51
    assert 'products' in TENANT_TABLES
    assert 'python_catalog' in TENANT_TABLES


def test_build_table_predicate_default():
    pred = _build_table_predicate('products', '"products"')
    assert pred == "tenant_id = current_setting('app.current_tenant_id', true)"


def test_build_table_predicate_specialized():
    pred_op = _build_table_predicate('python_operator_profiles', '"python_operator_profiles"')
    assert 'python_users parent' in pred_op
    pred_task = _build_table_predicate('python_task_performance', '"python_task_performance"')
    assert 'python_labor_shifts parent' in pred_task
    assert 'python_stock_movements parent' in pred_task
    assert 'cycle_count_records parent' in pred_task


@pytest.mark.asyncio
async def test_install_tenant_policies_skips_non_postgres():
    conn = MagicMock()
    conn.dialect.name = 'sqlite'
    conn.execute = AsyncMock()
    await install_tenant_policies(conn)
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_install_tenant_policies_postgres():
    conn = MagicMock()
    conn.dialect.name = 'postgresql'
    conn.dialect.identifier_preparer.quote = lambda t: f'"{t}"'
    executed_statements = []

    async def mock_execute(stmt):
        executed_statements.append(str(stmt))

    conn.execute = mock_execute
    await install_tenant_policies(conn)

    # 51 tables * 4 statements = 204 statements, plus 4 for cycle_count_line_items, plus 4 for outbox_events = 212 statements
    assert len(executed_statements) == 212
    assert any('ALTER TABLE "products" ENABLE ROW LEVEL SECURITY' in stmt for stmt in executed_statements)
    assert any('ALTER TABLE cycle_count_line_items ENABLE ROW LEVEL SECURITY' in stmt for stmt in executed_statements)
    assert any('ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY' in stmt for stmt in executed_statements)
