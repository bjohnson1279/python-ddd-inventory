from sqlalchemy import text


async def install_tenant_policies(conn):
    if conn.dialect.name != 'postgresql':
        return
    tables = ['products', 'python_catalog', 'python_variant_links', 'python_barcodes',
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
              'python_integration_connections', 'python_integration_orders', 'python_integration_receipts', 'python_integration_deliveries']
    quote = conn.dialect.identifier_preparer.quote
    predicate = "tenant_id = current_setting('app.current_tenant_id', true)"
    for table in tables:
        name = quote(table)
        row_predicate = predicate
        if table in ('python_integration_orders', 'python_integration_receipts', 'python_integration_deliveries'):
            row_predicate += f" AND EXISTS (SELECT 1 FROM python_integration_connections parent WHERE parent.id = {name}.connection_id AND parent.tenant_id = {name}.tenant_id)"
        if table == 'python_integration_deliveries':
            row_predicate += f" AND EXISTS (SELECT 1 FROM outbox_events parent WHERE parent.id = {name}.event_id AND parent.payload->>'tenantId' = {name}.tenant_id)"
        if table == 'python_integration_receipts':
            row_predicate += f" AND EXISTS (SELECT 1 FROM python_integration_orders parent WHERE parent.id = {name}.order_id AND parent.tenant_id = {name}.tenant_id AND parent.connection_id = {name}.connection_id)"
        if table == 'python_journals':
            row_predicate += " AND (source_id IS NULL OR EXISTS (SELECT 1 FROM python_stock_movements m WHERE m.id = python_journals.source_id AND m.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_journal_lines':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_journals j WHERE j.id = journal_id AND j.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_financial_sources':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_stock_movements m WHERE m.id = movement_id AND m.tenant_id = current_setting('app.current_tenant_id', true))"
            row_predicate += " AND (journal_id IS NULL OR EXISTS (SELECT 1 FROM python_journals j WHERE j.id = python_financial_sources.journal_id AND j.source_id = python_financial_sources.movement_id AND j.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_financial_documents':
            row_predicate += " AND (supplier_id IS NULL OR EXISTS (SELECT 1 FROM python_suppliers s WHERE s.id = supplier_id AND s.tenant_id = current_setting('app.current_tenant_id', true)))"
            row_predicate += " AND (journal_id IS NULL OR EXISTS (SELECT 1 FROM python_journals j WHERE j.id = python_financial_documents.journal_id AND j.reference_id = python_financial_documents.id AND j.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table in ('python_document_sources', 'python_financial_settlements', 'python_financial_credits'):
            row_predicate += f" AND EXISTS (SELECT 1 FROM python_financial_documents d WHERE d.id = {table}.document_id AND d.tenant_id = current_setting('app.current_tenant_id', true))"
        if table in ('python_document_sources', 'python_financial_credits'):
            row_predicate += " AND EXISTS (SELECT 1 FROM python_financial_sources s WHERE s.movement_id = source_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_financial_settlements':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_journals j WHERE j.id = python_financial_settlements.journal_id AND j.kind IN ('SETTLEMENT','REFUND') AND j.reference_id = python_financial_settlements.reference AND j.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_financial_credits':
            row_predicate += " AND (journal_id IS NULL OR EXISTS (SELECT 1 FROM python_journals j WHERE j.id=python_financial_credits.journal_id AND j.kind='CREDIT' AND j.reference_id=python_financial_credits.reference AND j.tenant_id=current_setting('app.current_tenant_id', true)))"
        if table in ('python_serial_tracking', 'python_serials', 'python_kits'):
            row_predicate += f" AND EXISTS (SELECT 1 FROM products p WHERE p.sku = {table}.sku AND p.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_serials':
            row_predicate += " AND EXISTS (SELECT 1 FROM products p WHERE p.id = python_serials.variant_id AND p.sku = python_serials.sku AND p.tenant_id = current_setting('app.current_tenant_id', true))"
            row_predicate += " AND (layer_id IS NULL OR EXISTS (SELECT 1 FROM python_cost_layers l WHERE l.id = python_serials.layer_id AND l.sku = python_serials.sku AND l.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_kits':
            row_predicate += " AND NOT EXISTS (SELECT 1 FROM json_array_elements(python_kits.components) c WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.sku = c ->> 'sku' AND p.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_kit_builds':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_kits k JOIN python_cost_layers l ON l.sku = k.sku WHERE k.id = python_kit_builds.kit_id AND l.id = python_kit_builds.layer_id AND k.tenant_id = current_setting('app.current_tenant_id', true) AND l.tenant_id = k.tenant_id)"
            row_predicate += " AND NOT EXISTS (SELECT 1 FROM json_array_elements(python_kit_builds.components) c WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.sku = c ->> 'sku' AND p.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_uom_configs':
            row_predicate += " AND EXISTS (SELECT 1 FROM products p WHERE p.sku = python_uom_configs.sku AND p.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_stock_transfers':
            row_predicate += " AND NOT EXISTS (SELECT 1 FROM json_array_elements(python_stock_transfers.items) i WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.sku = i ->> 'sku' AND p.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_notifications':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_users u WHERE u.id = user_id AND u.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_cost_layers':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_stock s WHERE s.id = stock_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
        if table in ('python_purchase_orders', 'python_reorder_policies'):
            row_predicate += " AND EXISTS (SELECT 1 FROM python_suppliers s WHERE s.id = supplier_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_supplier_asns':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_purchase_orders p WHERE p.id = po_id AND p.supplier_id = python_supplier_asns.supplier_id AND p.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_returns':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_stock_movements m WHERE m.id = source_movement_id AND m.tenant_id = current_setting('app.current_tenant_id', true))"
            row_predicate += " AND (supplier_id IS NULL OR EXISTS (SELECT 1 FROM python_suppliers s WHERE s.id = python_returns.supplier_id AND s.tenant_id = current_setting('app.current_tenant_id', true)))"
        if table == 'python_return_quarantine':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_returns r WHERE r.id = return_id AND r.tenant_id = current_setting('app.current_tenant_id', true)) AND EXISTS (SELECT 1 FROM python_stock s WHERE s.id = stock_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
            row_predicate += " AND EXISTS (SELECT 1 FROM python_returns r JOIN python_stock_movements m ON m.id = r.source_movement_id WHERE r.id = python_return_quarantine.return_id AND m.result ->> 'id' = python_return_quarantine.stock_id)"
        await conn.execute(text(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY'))
        await conn.execute(text(f'ALTER TABLE {name} FORCE ROW LEVEL SECURITY'))
        await conn.execute(text(f'DROP POLICY IF EXISTS python_tenant_isolation ON {name}'))
        await conn.execute(text(f'CREATE POLICY python_tenant_isolation ON {name} USING ({row_predicate}) WITH CHECK ({row_predicate})'))
    await conn.execute(text('ALTER TABLE cycle_count_line_items ENABLE ROW LEVEL SECURITY'))
    await conn.execute(text('ALTER TABLE cycle_count_line_items FORCE ROW LEVEL SECURITY'))
    await conn.execute(text('DROP POLICY IF EXISTS python_tenant_isolation ON cycle_count_line_items'))
    check = "EXISTS (SELECT 1 FROM cycle_count_records r WHERE r.id = record_id AND r.tenant_id = current_setting('app.current_tenant_id', true))"
    await conn.execute(text(f'CREATE POLICY python_tenant_isolation ON cycle_count_line_items USING ({check}) WITH CHECK ({check})'))
    await conn.execute(text('ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY'))
    await conn.execute(text('ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY'))
    await conn.execute(text('DROP POLICY IF EXISTS python_tenant_isolation ON outbox_events'))
    outbox_check = "payload ->> 'tenantId' = current_setting('app.current_tenant_id', true)"
    outbox_check += " AND (stream_key IS NULL OR EXISTS (SELECT 1 FROM python_outbox_streams s WHERE s.key = outbox_events.stream_key AND s.tenant_id = current_setting('app.current_tenant_id', true) AND s.aggregate_type = outbox_events.aggregate_type AND s.aggregate_id = outbox_events.aggregate_id))"
    await conn.execute(text(f'CREATE POLICY python_tenant_isolation ON outbox_events USING ({outbox_check}) WITH CHECK ({outbox_check})'))
