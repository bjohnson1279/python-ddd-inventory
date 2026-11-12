import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import text
from src.infrastructure.auth.rls import install_tenant_policies


@pytest.mark.asyncio
async def test_install_tenant_policies_skips_non_postgresql():
    conn = MagicMock()
    conn.dialect.name = "sqlite"
    conn.execute = AsyncMock()

    await install_tenant_policies(conn)

    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_install_tenant_policies_postgresql_executes_expected_ddl():
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    conn.dialect.identifier_preparer.quote = lambda x: f'"{x}"'
    conn.execute = AsyncMock()

    await install_tenant_policies(conn)

    executed_statements = [call.args[0].text for call in conn.execute.call_args_list]

    # 51 tables * 4 statements + 4 for cycle_count_line_items + 4 for outbox_events = 212 statements
    assert len(executed_statements) == 212

    # Check products base policy
    assert 'ALTER TABLE "products" ENABLE ROW LEVEL SECURITY' in executed_statements
    assert 'ALTER TABLE "products" FORCE ROW LEVEL SECURITY' in executed_statements
    assert 'DROP POLICY IF EXISTS python_tenant_isolation ON "products"' in executed_statements
    assert any('CREATE POLICY python_tenant_isolation ON "products"' in s for s in executed_statements)

    # Check specialized predicates
    def get_policy(table_name):
        for s in executed_statements:
            if s.startswith(f'CREATE POLICY python_tenant_isolation ON "{table_name}"') or s.startswith(f'CREATE POLICY python_tenant_isolation ON {table_name}'):
                return s
        raise AssertionError(f"Policy for {table_name} not found")

    assert "EXISTS (SELECT 1 FROM python_users parent WHERE parent.id=\"python_operator_profiles\".user_id" in get_policy("python_operator_profiles")
    assert "EXISTS (SELECT 1 FROM python_operator_profiles parent WHERE parent.id=\"python_labor_shifts\".operator_id" in get_policy("python_labor_shifts")

    task_perf_policy = get_policy("python_task_performance")
    assert "shift_id" in task_perf_policy
    assert "movement_id" in task_perf_policy
    assert "count_id" in task_perf_policy

    assert "requested_by" in get_policy("python_report_jobs")
    assert "created_by" in get_policy("python_report_schedules")

    notices_policy = get_policy("python_alert_notices")
    assert "condition_id" in notices_policy
    assert "notification_id" in notices_policy

    assert "connection_id" in get_policy("python_integration_orders")
    assert "payload->>'tenantId'" in get_policy("python_integration_deliveries")
    assert "order_id" in get_policy("python_integration_receipts")

    assert "python_journals" in get_policy("python_journals")
    assert "python_journal_lines" in get_policy("python_journal_lines")
    assert "python_financial_sources" in get_policy("python_financial_sources")
    assert "python_financial_documents" in get_policy("python_financial_documents")
    assert "python_financial_settlements" in get_policy("python_financial_settlements")
    assert "python_financial_credits" in get_policy("python_financial_credits")

    assert "python_serial_tracking" in get_policy("python_serial_tracking")
    assert "variant_id" in get_policy("python_serials")
    assert "json_array_elements(python_kits.components)" in get_policy("python_kits")
    assert "json_array_elements(python_kit_builds.components)" in get_policy("python_kit_builds")
    assert "python_uom_configs" in get_policy("python_uom_configs")
    assert "json_array_elements(python_stock_transfers.items)" in get_policy("python_stock_transfers")
    assert "python_notifications" in get_policy("python_notifications")
    assert "python_cost_layers" in get_policy("python_cost_layers")
    assert "python_purchase_orders" in get_policy("python_purchase_orders")
    assert "python_supplier_asns" in get_policy("python_supplier_asns")
    assert "source_movement_id" in get_policy("python_returns")
    assert "python_return_quarantine" in get_policy("python_return_quarantine")

    # Check non-table-loop items: cycle_count_line_items & outbox_events
    cycle_items_policy = get_policy("cycle_count_line_items")
    assert "cycle_count_records" in cycle_items_policy
    assert 'ALTER TABLE cycle_count_line_items ENABLE ROW LEVEL SECURITY' in executed_statements

    outbox_policy = get_policy("outbox_events")
    assert "payload ->> 'tenantId'" in outbox_policy
    assert "python_outbox_streams" in outbox_policy
    assert 'ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY' in executed_statements
