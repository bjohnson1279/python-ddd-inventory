from sqlalchemy import text

TENANT_TABLES = [
    'products', 'python_catalog', 'python_variant_links', 'python_barcodes',
    'python_stock', 'python_stock_movements', 'python_cost_layers', 'python_accounting_config',
    'python_report_definitions', 'python_notifications', 'python_security_audit',
    'cycle_count_plans', 'cycle_count_records', 'python_count_requests',
    'python_suppliers', 'python_purchase_orders', 'python_procurement_actions',
    'python_reorder_policies', 'python_supplier_asns', 'python_returns',
    'python_return_actions', 'python_return_quarantine', 'python_stock_transfers',
    'python_inventory_actions', 'python_uom_configs', 'python_outbox_streams',
    'python_serial_tracking', 'python_serials', 'python_kits', 'python_kit_builds',
    'python_ledger_state', 'python_journals', 'python_journal_lines', 'python_financial_sources',
    'python_financial_documents', 'python_document_sources', 'python_financial_settlements', 'python_financial_credits',
    'python_integration_connections', 'python_integration_orders', 'python_integration_receipts', 'python_integration_deliveries',
    'python_report_jobs', 'python_report_schedules', 'python_alert_config', 'python_alert_conditions', 'python_alert_notices',
    'python_operator_profiles', 'python_labor_shifts', 'python_task_performance', 'python_staffing_plans',
]


def _build_table_predicate(table: str, name: str) -> str:
    predicate = "tenant_id = current_setting('app.current_tenant_id', true)"
    if table == 'python_operator_profiles':
        predicate += f" AND EXISTS (SELECT 1 FROM python_users parent WHERE parent.id={name}.user_id AND parent.tenant_id={name}.tenant_id)"
    if table == 'python_labor_shifts':
        predicate += f" AND EXISTS (SELECT 1 FROM python_operator_profiles parent WHERE parent.id={name}.operator_id AND parent.tenant_id={name}.tenant_id)"
    if table == 'python_task_performance':
        predicate += f" AND EXISTS (SELECT 1 FROM python_labor_shifts parent WHERE parent.id={name}.shift_id AND parent.tenant_id={name}.tenant_id)"
        predicate += f" AND (movement_id IS NULL OR EXISTS (SELECT 1 FROM python_stock_movements parent WHERE parent.id={name}.movement_id AND parent.tenant_id={name}.tenant_id))"
        predicate += f" AND (count_id IS NULL OR EXISTS (SELECT 1 FROM cycle_count_records parent WHERE parent.id={name}.count_id AND parent.tenant_id={name}.tenant_id))"
    if table in ('python_report_jobs', 'python_report_schedules'):
        predicate += f" AND EXISTS (SELECT 1 FROM python_report_definitions parent WHERE parent.id={name}.report_id AND parent.tenant_id={name}.tenant_id)"
        actor = 'requested_by' if table == 'python_report_jobs' else 'created_by'
        predicate += f" AND EXISTS (SELECT 1 FROM python_users parent WHERE parent.id={name}.{actor} AND parent.tenant_id={name}.tenant_id)"
    if table == 'python_alert_notices':
        predicate += f" AND EXISTS (SELECT 1 FROM python_alert_conditions parent WHERE parent.id={name}.condition_id AND parent.tenant_id={name}.tenant_id)"
        predicate += f" AND EXISTS (SELECT 1 FROM python_notifications parent WHERE parent.id={name}.notification_id AND parent.tenant_id={name}.tenant_id)"
    if table in ('python_integration_orders', 'python_integration_receipts', 'python_integration_deliveries'):
        predicate += f" AND EXISTS (SELECT 1 FROM python_integration_connections parent WHERE parent.id = {name}.connection_id AND parent.tenant_id = {name}.tenant_id)"
    if table == 'python_integration_deliveries':
        predicate += f" AND EXISTS (SELECT 1 FROM outbox_events parent WHERE parent.id = {name}.event_id AND parent.payload->>'tenantId' = {name}.tenant_id)"
    if table == 'python_integration_receipts':
        predicate += f" AND EXISTS (SELECT 1 FROM python_integration_orders parent WHERE parent.id = {name}.order_id AND parent.tenant_id = {name}.tenant_id AND parent.connection_id = {name}.connection_id)"
    if table == 'python_journals':
        predicate += " AND (source_id IS NULL OR EXISTS (SELECT 1 FROM python_stock_movements m WHERE m.id = python_journals.source_id AND m.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_journal_lines':
        predicate += " AND EXISTS (SELECT 1 FROM python_journals j WHERE j.id = journal_id AND j.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_financial_sources':
        predicate += " AND EXISTS (SELECT 1 FROM python_stock_movements m WHERE m.id = movement_id AND m.tenant_id = current_setting('app.current_tenant_id', true))"
        predicate += " AND (journal_id IS NULL OR EXISTS (SELECT 1 FROM python_journals j WHERE j.id = python_financial_sources.journal_id AND j.source_id = python_financial_sources.movement_id AND j.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_financial_documents':
        predicate += " AND (supplier_id IS NULL OR EXISTS (SELECT 1 FROM python_suppliers s WHERE s.id = supplier_id AND s.tenant_id = current_setting('app.current_tenant_id', true)))"
        predicate += " AND (journal_id IS NULL OR EXISTS (SELECT 1 FROM python_journals j WHERE j.id = python_financial_documents.journal_id AND j.reference_id = python_financial_documents.id AND j.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table in ('python_document_sources', 'python_financial_settlements', 'python_financial_credits'):
        predicate += f" AND EXISTS (SELECT 1 FROM python_financial_documents d WHERE d.id = {table}.document_id AND d.tenant_id = current_setting('app.current_tenant_id', true))"
    if table in ('python_document_sources', 'python_financial_credits'):
        predicate += " AND EXISTS (SELECT 1 FROM python_financial_sources s WHERE s.movement_id = source_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_financial_settlements':
        predicate += " AND EXISTS (SELECT 1 FROM python_journals j WHERE j.id = python_financial_settlements.journal_id AND j.kind IN ('SETTLEMENT','REFUND') AND j.reference_id = python_financial_settlements.reference AND j.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_financial_credits':
        predicate += " AND (journal_id IS NULL OR EXISTS (SELECT 1 FROM python_journals j WHERE j.id=python_financial_credits.journal_id AND j.kind='CREDIT' AND j.reference_id=python_financial_credits.reference AND j.tenant_id=current_setting('app.current_tenant_id', true)))"
    if table in ('python_serial_tracking', 'python_serials', 'python_kits'):
        predicate += f" AND EXISTS (SELECT 1 FROM products p WHERE p.sku = {table}.sku AND p.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_serials':
        predicate += " AND EXISTS (SELECT 1 FROM products p WHERE p.id = python_serials.variant_id AND p.sku = python_serials.sku AND p.tenant_id = current_setting('app.current_tenant_id', true))"
        predicate += " AND (layer_id IS NULL OR EXISTS (SELECT 1 FROM python_cost_layers l WHERE l.id = python_serials.layer_id AND l.sku = python_serials.sku AND l.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_kits':
        predicate += " AND NOT EXISTS (SELECT 1 FROM json_array_elements(python_kits.components) c WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.sku = c ->> 'sku' AND p.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_kit_builds':
        predicate += " AND EXISTS (SELECT 1 FROM python_kits k JOIN python_cost_layers l ON l.sku = k.sku WHERE k.id = python_kit_builds.kit_id AND l.id = python_kit_builds.layer_id AND k.tenant_id = current_setting('app.current_tenant_id', true) AND l.tenant_id = k.tenant_id)"
        predicate += " AND NOT EXISTS (SELECT 1 FROM json_array_elements(python_kit_builds.components) c WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.sku = c ->> 'sku' AND p.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_uom_configs':
        predicate += " AND EXISTS (SELECT 1 FROM products p WHERE p.sku = python_uom_configs.sku AND p.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_stock_transfers':
        predicate += " AND NOT EXISTS (SELECT 1 FROM json_array_elements(python_stock_transfers.items) i WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.sku = i ->> 'sku' AND p.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_notifications':
        predicate += " AND EXISTS (SELECT 1 FROM python_users u WHERE u.id = user_id AND u.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_cost_layers':
        predicate += " AND EXISTS (SELECT 1 FROM python_stock s WHERE s.id = stock_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
    if table in ('python_purchase_orders', 'python_reorder_policies'):
        predicate += " AND EXISTS (SELECT 1 FROM python_suppliers s WHERE s.id = supplier_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_supplier_asns':
        predicate += " AND EXISTS (SELECT 1 FROM python_purchase_orders p WHERE p.id = po_id AND p.supplier_id = python_supplier_asns.supplier_id AND p.tenant_id = current_setting('app.current_tenant_id', true))"
    if table == 'python_returns':
        predicate += " AND EXISTS (SELECT 1 FROM python_stock_movements m WHERE m.id = source_movement_id AND m.tenant_id = current_setting('app.current_tenant_id', true))"
        predicate += " AND (supplier_id IS NULL OR EXISTS (SELECT 1 FROM python_suppliers s WHERE s.id = python_returns.supplier_id AND s.tenant_id = current_setting('app.current_tenant_id', true)))"
    if table == 'python_return_quarantine':
        predicate += " AND EXISTS (SELECT 1 FROM python_returns r WHERE r.id = return_id AND r.tenant_id = current_setting('app.current_tenant_id', true)) AND EXISTS (SELECT 1 FROM python_stock s WHERE s.id = stock_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
        predicate += " AND EXISTS (SELECT 1 FROM python_returns r JOIN python_stock_movements m ON m.id = r.source_movement_id WHERE r.id = python_return_quarantine.return_id AND m.result ->> 'id' = python_return_quarantine.stock_id)"
    return predicate


async def _apply_policy(conn, table_identifier: str, predicate: str) -> None:
    await conn.execute(text(f'ALTER TABLE {table_identifier} ENABLE ROW LEVEL SECURITY'))
    await conn.execute(text(f'ALTER TABLE {table_identifier} FORCE ROW LEVEL SECURITY'))
    await conn.execute(text(f'DROP POLICY IF EXISTS python_tenant_isolation ON {table_identifier}'))
    await conn.execute(text(f'CREATE POLICY python_tenant_isolation ON {table_identifier} USING ({predicate}) WITH CHECK ({predicate})'))


async def _install_special_policies(conn) -> None:
    check = "EXISTS (SELECT 1 FROM cycle_count_records r WHERE r.id = record_id AND r.tenant_id = current_setting('app.current_tenant_id', true))"
    await _apply_policy(conn, 'cycle_count_line_items', check)

    outbox_check = "payload ->> 'tenantId' = current_setting('app.current_tenant_id', true)"
    outbox_check += " AND (stream_key IS NULL OR EXISTS (SELECT 1 FROM python_outbox_streams s WHERE s.key = outbox_events.stream_key AND s.tenant_id = current_setting('app.current_tenant_id', true) AND s.aggregate_type = outbox_events.aggregate_type AND s.aggregate_id = outbox_events.aggregate_id))"
    await _apply_policy(conn, 'outbox_events', outbox_check)


async def install_tenant_policies(conn):
    if conn.dialect.name != 'postgresql':
        return
    quote = conn.dialect.identifier_preparer.quote
    for table in TENANT_TABLES:
        name = quote(table)
        row_predicate = _build_table_predicate(table, name)
        await _apply_policy(conn, name, row_predicate)
    await _install_special_policies(conn)
