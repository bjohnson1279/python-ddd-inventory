from sqlalchemy import text


async def install_tenant_policies(conn):
    if conn.dialect.name != 'postgresql':
        return
    tables = ['products', 'python_catalog', 'python_variant_links', 'python_barcodes',
              'python_stock', 'python_stock_movements', 'python_cost_layers', 'python_accounting_config',
              'python_report_definitions', 'python_notifications', 'python_security_audit',
              'cycle_count_plans', 'cycle_count_records']
    quote = conn.dialect.identifier_preparer.quote
    predicate = "tenant_id = current_setting('app.current_tenant_id', true)"
    for table in tables:
        name = quote(table)
        row_predicate = predicate
        if table == 'python_notifications':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_users u WHERE u.id = user_id AND u.tenant_id = current_setting('app.current_tenant_id', true))"
        if table == 'python_cost_layers':
            row_predicate += " AND EXISTS (SELECT 1 FROM python_stock s WHERE s.id = stock_id AND s.tenant_id = current_setting('app.current_tenant_id', true))"
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
    await conn.execute(text(f'CREATE POLICY python_tenant_isolation ON outbox_events USING ({outbox_check}) WITH CHECK ({outbox_check})'))
