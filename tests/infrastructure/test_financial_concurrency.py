import asyncio
import json
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.infrastructure.test_outbox_contract import outbox_database


@pytest.mark.asyncio
async def test_balanced_atomic_financial_posting_concurrency_and_restricted_database_guards(outbox_database):
    client, sessions, runtime, backend = outbox_database
    day = datetime.now(timezone.utc).date().isoformat()
    async def enroll(tenant):
        setup = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
            'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
        assert setup.status_code == 201, setup.text
        token = (await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        product = await client.post('/products', headers=headers, json={'sku': 'ITEM', 'name': 'Item', 'price_amount': '99.00'})
        assert product.status_code == 201, product.text
        active = await client.post('/accounting/financial/activate', headers=headers, json={'expectedVersion': 0, 'mutationId': 'activate', 'reason': 'Reviewed opening'})
        assert active.status_code == 200, active.text
        return headers
    a, b = await enroll('store-a'), await enroll('store-b')
    receive = {'sku': 'ITEM', 'locationId': 'shop', 'quantity': 20, 'unitCostCents': 100, 'mutationId': 'receive-once'}
    receipts = await asyncio.gather(*(client.post('/inventory/receive', headers=a, json=receive) for _ in range(2)))
    assert [r.status_code for r in receipts] == [200, 200], [r.text for r in receipts]
    assert receipts[0].json() == receipts[1].json()
    lines = [{'accountCode': '1000', 'amountCents': 1, 'type': 'debit'}, {'accountCode': '3000', 'amountCents': 1, 'type': 'credit'}]
    manual = {'date': day, 'description': 'Reviewed cash opening', 'method': 'ACCRUAL', 'reason': 'Statement', 'lines': lines}
    entries = await asyncio.gather(*(client.post('/accounting/journals', headers=a, json={**manual, 'mutationId': 'cash-' + str(i)}) for i in range(2)))
    assert [r.status_code for r in entries] == [201, 201], [r.text for r in entries]
    sales = await asyncio.gather(*(client.post('/inventory/dispatch', headers=a, json={'sku': 'ITEM', 'locationId': 'shop', 'quantity': 11,
        'expectedVersion': 1, 'mutationId': 'sale-' + str(i)}) for i in range(2)))
    assert sorted(r.status_code for r in sales) == [200, 409], [r.text for r in sales]
    source = next(s for s in (await client.get('/accounting/sources', headers=a)).json() if s['kind'] == 'SALE')
    document = await client.post('/accounting/documents', headers=a, json={'kind': 'SALE', 'date': day, 'reference': 'sale-doc',
        'counterparty': 'Customer', 'amountCents': 1450, 'sourceIds': [source['id']], 'mutationId': 'commercial-sale', 'reason': 'Actual sale price'})
    assert document.status_code == 201, document.text
    endpoint = '/accounting/documents/' + document.json()['id'] + '/settle'
    settlement = {'date': day, 'expectedVersion': 0, 'amountCents': 500, 'reference': 'bank-receipt', 'mutationId': 'cash-receipt', 'reason': 'Confirmed payment'}
    payments = await asyncio.gather(*(client.post(endpoint, headers=a, json=settlement) for _ in range(2)))
    assert [r.status_code for r in payments] == [200, 200], [r.text for r in payments]
    assert payments[0].json() == payments[1].json()
    sold_layer = source['facts']['result']['consumedLayers'][0]['layerId']
    rma = await client.post('/returns/rmas', headers=a, json={'sourceMovementId': source['id'], 'reason': 'Customer return',
        'items': [{'layerId': sold_layer, 'quantity': 1, 'disposition': 'RESTOCK'}], 'mutationId': 'return'} )
    assert rma.status_code == 201, rma.text
    authorized = await client.post('/returns/rmas/' + rma.json()['id'] + '/authorize', headers=a,
        json={'expectedVersion': 0, 'mutationId': 'approve-return', 'reason': 'Verified return'})
    assert authorized.status_code == 200, authorized.text
    returned = await client.post('/returns/rmas/' + rma.json()['id'] + '/receive', headers=a,
        json={'expectedVersion': authorized.json()['version'], 'receiptId': 'return-receipt', 'items': [{'layerId': sold_layer, 'quantity': 1}]})
    assert returned.status_code == 200, returned.text
    return_source = next(s for s in (await client.get('/accounting/sources', headers=a)).json() if s['kind'] == 'CUSTOMER_RETURN')
    credited = await client.post('/accounting/documents/' + document.json()['id'] + '/credit', headers=a,
        json={'date': day, 'expectedVersion': 1, 'sourceId': return_source['id'], 'amountCents': 1000,
            'reference': 'customer-credit', 'mutationId': 'customer-credit', 'reason': 'Confirmed goodwill credit'})
    assert credited.status_code == 200 and credited.json()['refundDueCents'] == 50, credited.text
    refund = {'date': day, 'expectedVersion': 2, 'amountCents': 50, 'reference': 'bank-refund', 'mutationId': 'refund-once', 'reason': 'Actual refund paid'}
    refunds = await asyncio.gather(*(client.post('/accounting/documents/' + document.json()['id'] + '/refund', headers=a, json=refund) for _ in range(2)))
    assert [r.status_code for r in refunds] == [200, 200], [r.text for r in refunds]
    assert refunds[0].json() == refunds[1].json() and refunds[0].json()['status'] == 'SETTLED'
    ledger = (await client.get('/accounting/ledger', headers=a)).json()
    assert [j['number'] for j in ledger] == list(range(1, len(ledger) + 1))
    assert all(j['totalDebitsCents'] == j['totalCreditsCents'] > 0 for j in ledger)
    trial = (await client.get('/accounting/trial-balance', headers=a)).json()
    assert trial['isBalanced'] and trial['complete']
    assert (await client.get('/accounting/financial/reconciliation', headers=a)).json()['isReconciled']
    assert (await client.get('/accounting/journals/' + ledger[0]['id'], headers=b)).status_code == 404
    assert (await client.post(endpoint, headers=b, json=settlement)).status_code == 404
    assert (await client.get('/accounting/ledger', headers=b)).json() == []
    allocated = await client.post('/inventory/allocate', headers=a, json={'sku': 'ITEM', 'locationId': 'shop', 'quantity': 1, 'mutationId': 'reservation'})
    assert allocated.status_code == 200, allocated.text
    unused_source = next(s for s in (await client.get('/accounting/sources', headers=a)).json() if s['status'] == 'NO_EFFECT')
    if backend == 'postgres':
        async with runtime.begin() as conn:
            assert (await conn.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user'))).one() == (False, False)
            for table in ('python_ledger_state', 'python_journals', 'python_journal_lines', 'python_financial_sources',
                          'python_financial_documents', 'python_document_sources', 'python_financial_settlements', 'python_financial_credits'):
                assert await conn.scalar(text('SELECT count(*) FROM ' + table)) == 0
                assert await conn.scalar(text("SELECT relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)"), {'name': table})
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id','store-a',true)"))
                await conn.execute(text('UPDATE python_journals SET description=:description WHERE id=:id'), {'id': ledger[0]['id'], 'description': 'Tampered'})
        assert denied.value.orig.sqlstate == '55000'
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id','store-b',true)"))
                await conn.execute(text('INSERT INTO python_journal_lines (id,tenant_id,journal_id,position,account_code,side,amount_cents,memo) VALUES (:id,\'store-b\',:parent,0,\'1000\',\'debit\',1,\'\')'),
                    {'id': str(uuid.uuid4()), 'parent': ledger[0]['id']})
        assert denied.value.orig.sqlstate in ('23514', '42501')
        snapshot = [{'id': str(uuid.uuid4()), **line, 'memo': ''} for line in lines]
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id','store-b',true)"))
                await conn.execute(text("INSERT INTO python_journals (id,tenant_id,number,date,posted_at,description,actor_id,method,kind,source_id,lines,line_count,total_debits,total_credits) "
                    "VALUES (:id,'store-b',99,:date,now(),'Foreign source','actor','ACCRUAL','INVENTORY',:source,:lines,2,1,1)"),
                    {'id': str(uuid.uuid4()), 'date': datetime.now(timezone.utc).date(), 'source': unused_source['id'], 'lines': json.dumps(snapshot)})
        assert denied.value.orig.sqlstate == '42501'
