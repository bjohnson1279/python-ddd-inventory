from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest
from sqlalchemy import select, update, text
from sqlalchemy.exc import DBAPIError
from src.infrastructure.financial_models import JournalModel, JournalLineModel, FinancialSourceModel

pytestmark = pytest.mark.asyncio


def today():
    return datetime.now(timezone.utc).date().isoformat()


def ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def product(client, sku='ITEM'):
    return ok(client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '99.00'}), 201)


def activate(client, key='activate'):
    version = ok(client.get('/accounting/financial/status'))['version']
    return client.post('/accounting/financial/activate', json={'expectedVersion': version, 'mutationId': key, 'reason': 'Reviewed opening balances'})


def receive(client, quantity=5, cost=100, key='receipt', sku='ITEM', **fields):
    return client.post('/inventory/receive', json={'sku': sku, 'quantity': quantity, 'locationId': 'shop',
        'unitCostCents': cost, 'mutationId': key, **fields})


def sources(client, operation):
    return [row for row in ok(client.get('/accounting/sources?limit=1000')) if row['facts']['operation'] == operation]


def balances(client):
    result = ok(client.get('/accounting/trial-balance'))
    assert result['isBalanced']
    return {row['accountCode']: row['balanceCents'] for row in result['accounts']}


def manual(client, key='manual', amount=100, **fields):
    payload = {'date': today(), 'method': 'ACCRUAL', 'description': 'Cash opening', 'reason': 'Reviewed statement', 'mutationId': key,
        'lines': [{'accountCode': '1000', 'amountCents': amount, 'type': 'debit'}, {'accountCode': '3000', 'amountCents': amount, 'type': 'credit'}], **fields}
    return client.post('/accounting/journals', json=payload), payload


async def test_opening_inventory_is_explicit_and_postings_follow_actual_stock_cost(client):
    product(client)
    assert ok(client.get('/accounting/financial/status'))['active'] is False
    assert client.get('/accounting/trial-balance').status_code == 409
    ok(receive(client))
    assert ok(client.get('/accounting/ledger')) == []
    opening = ok(activate(client))
    assert opening['openingInventory']['onHandValueCents'] == 500
    assert balances(client)['1200'] == 500 and balances(client)['3000'] == -500
    original = {'expectedVersion': 0, 'mutationId': 'activate', 'reason': 'Reviewed opening balances'}
    assert ok(client.post('/accounting/financial/activate', json=original)) == opening
    ok(receive(client, 2, 111, 'second'))
    sale = ok(client.post('/inventory/dispatch', json={'sku': 'ITEM', 'locationId': 'shop', 'quantity': 3, 'mutationId': 'sale'}))
    assert sale['costOfGoodsSoldCents'] == 300
    ledger = balances(client)
    assert ledger['1200'] == 422 and ledger['2100'] == -222 and ledger['5000'] == 300
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']
    assert ok(client.get('/accounting/income-statement'))['revenueCents'] == 0
    assert ok(client.get('/accounting/balance-sheet'))['isBalanced']
    assert client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'FIFO', 'accountingMethod': 'CASH'}).status_code == 409


@pytest.mark.parametrize('method', ['ACCRUAL', 'CASH'])
async def test_real_commercial_documents_and_partial_settlements_do_not_invent_cash(client, method):
    product(client)
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'FIFO', 'accountingMethod': method}))
    ok(activate(client))
    ok(receive(client, 2, 100))
    supplier = ok(client.post('/suppliers', json={'name': 'Vendor', 'mutationId': 'vendor'}), 201)
    payload = {'kind': 'PURCHASE', 'date': today(), 'reference': 'invoice-1', 'counterparty': 'Vendor', 'supplierId': supplier['id'],
        'amountCents': 200, 'sourceIds': [sources(client, 'receive')[0]['id']], 'mutationId': 'invoice', 'reason': 'Verified invoice'}
    invoice = ok(client.post('/accounting/documents', json=payload), 201)
    assert balances(client)['1000'] == 0
    assert balances(client)['2000'] == (-200 if method == 'ACCRUAL' else 0)
    assert ok(client.post('/accounting/documents', json=payload), 201) == invoice
    paid = ok(client.post('/accounting/documents/' + invoice['id'] + '/settle', json={'date': today(), 'expectedVersion': 0,
        'amountCents': 100, 'reference': 'bank-payment-1', 'mutationId': 'payment', 'reason': 'Confirmed bank payment'}))
    assert paid['outstandingCents'] == 100 and balances(client)['1000'] == -100
    assert client.post('/accounting/documents/' + invoice['id'] + '/settle', json={'date': today(), 'expectedVersion': 1,
        'amountCents': 101, 'reference': 'overpayment', 'mutationId': 'overpayment', 'reason': 'Overpay'}).status_code == 409
    ok(client.post('/inventory/dispatch', json={'sku': 'ITEM', 'locationId': 'shop', 'quantity': 1, 'mutationId': 'sale'}))
    sale_payload = {'kind': 'SALE', 'date': today(), 'reference': 'customer-sale', 'counterparty': 'Customer', 'amountCents': 350,
        'sourceIds': [sources(client, 'dispatch')[0]['id']], 'mutationId': 'sale-document', 'reason': 'Actual sale amount'}
    document = ok(client.post('/accounting/documents', json=sale_payload), 201)
    assert balances(client)['4000'] == (-350 if method == 'ACCRUAL' else 0)
    payment = {'date': today(), 'expectedVersion': 0, 'amountCents': 150, 'reference': 'customer-payment', 'mutationId': 'collection', 'reason': 'Confirmed customer payment'}
    collected = ok(client.post('/accounting/documents/' + document['id'] + '/settle', json=payment))
    assert ok(client.post('/accounting/documents/' + document['id'] + '/settle', json=payment)) == collected
    assert balances(client)['1000'] == 50
    assert balances(client)['4000'] == (-350 if method == 'ACCRUAL' else -150)
    assert balances(client)['1100'] == (200 if method == 'ACCRUAL' else 0)
    assert client.post('/accounting/documents', json={**sale_payload, 'reference': 'again', 'mutationId': 'repeat-source'}).status_code == 409


async def test_manual_journals_balance_replay_reverse_and_remain_immutable(client, db_session):
    ok(activate(client))
    response, payload = manual(client)
    entry = ok(response, 201)
    assert ok(client.post('/accounting/journals', json=payload), 201) == entry
    assert client.post('/accounting/journals', json={**payload, 'description': 'Changed'}).status_code == 409
    for value in (True, 1.5, -1, 0):
        assert manual(client, key='invalid', amount=value)[0].status_code == 422
    bad = {**payload, 'mutationId': 'unbalanced', 'lines': [{**line, 'amountCents': 101 if line['type'] == 'credit' else 100} for line in payload['lines']]}
    assert client.post('/accounting/journals', json=bad).status_code == 400
    assert manual(client, key='unknown', lines=[{'accountCode': '9999', 'amountCents': 1, 'type': 'debit'}, {'accountCode': '1000', 'amountCents': 1, 'type': 'credit'}])[0].status_code == 400
    reversal = {'date': today(), 'reason': 'Correct the entry', 'mutationId': 'reverse'}
    reversed_entry = ok(client.post('/accounting/journals/' + entry['id'] + '/reverse', json=reversal), 201)
    assert reversed_entry['reversalOf'] == entry['id'] and balances(client)['1000'] == 0
    assert ok(client.post('/accounting/journals/' + entry['id'] + '/reverse', json=reversal), 201) == reversed_entry
    assert client.post('/accounting/journals/' + entry['id'] + '/reverse', json={**reversal, 'mutationId': 'reverse-again'}).status_code == 409
    with pytest.raises(DBAPIError):
        await db_session.execute(update(JournalModel).where(JournalModel.id == entry['id']).values(description='Tampered'))
    await db_session.rollback()
    with pytest.raises(DBAPIError):
        await db_session.execute(update(JournalLineModel).where(JournalLineModel.journal_id == entry['id']).values(amount_cents=999))
    await db_session.rollback()
    general = ok(client.get('/accounting/general-ledger?accountCode=1000&limit=1&offset=1'))
    assert general['totalLines'] == 2 and general['closingBalanceCents'] == 0 and len(general['lines']) == 1


async def test_unpriced_sources_remain_visible_until_evidenced_resolution(client):
    product(client)
    ok(activate(client))
    ok(receive(client, 2, None, 'unpriced'))
    source = sources(client, 'receive')[0]
    assert source['status'] == 'UNPRICED'
    assert ok(client.get('/accounting/trial-balance'))['complete'] is False
    assert ok(client.get('/accounting/financial/reconciliation'))['complete'] is False
    layer = ok(client.get('/accounting/cost-layers'))[0]
    ok(client.post('/accounting/cost-layers/' + layer['id'] + '/price', json={'unitCostCents': 10, 'mutationId': 'price'}))
    assert balances(client)['1200'] == 20
    resolution = {'date': today(), 'expectedVersion': 0, 'mutationId': 'resolve', 'reason': 'Invoice confirms receipt cost; reclassify pricing variance',
        'referenceId': 'invoice', 'lines': [{'accountCode': '5200', 'amountCents': 20, 'type': 'debit'}, {'accountCode': '2100', 'amountCents': 20, 'type': 'credit'}]}
    resolved = ok(client.post('/accounting/sources/' + source['id'] + '/resolve', json=resolution))
    assert resolved['amountCents'] == 20 and balances(client)['1200'] == 20
    assert ok(client.post('/accounting/sources/' + source['id'] + '/resolve', json=resolution)) == resolved
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']


async def test_activation_refuses_unpriced_stock_and_ledger_failure_rolls_back_stock(client, db_session):
    product(client)
    ok(receive(client, 1, None))
    assert activate(client).status_code == 409
    layer = ok(client.get('/accounting/cost-layers'))[0]
    ok(client.post('/accounting/cost-layers/' + layer['id'] + '/price', json={'unitCostCents': 100, 'mutationId': 'price'}))
    ok(activate(client))
    before = len(ok(client.get('/accounting/ledger')))
    from src.application.stock_service import StockService
    with patch('src.infrastructure.financial_posting.post_journal', side_effect=RuntimeError('Injected posting failure')):
        with pytest.raises(RuntimeError):
            await StockService(db_session, 'tenant-1', 'operator').mutate('dispatch', 'ITEM', 'shop', 1, 'failed-sale')
    assert ok(client.get('/inventory'))[0]['quantity'] == 1
    assert len(ok(client.get('/accounting/ledger'))) == before
    assert sources(client, 'dispatch') == []


async def test_period_closure_backdating_and_source_completeness(client, monkeypatch):
    import src.infrastructure.financial_posting as posting
    import src.application.financial_service as financial
    historical = datetime.now(timezone.utc) - timedelta(days=2)
    monkeypatch.setattr(posting, 'database_now', lambda _: historical)
    monkeypatch.setattr(financial, 'database_now', lambda _: historical)
    product(client)
    ok(activate(client))
    ok(receive(client, 1, None))
    monkeypatch.setattr(posting, 'database_now', lambda _: datetime.now(timezone.utc))
    monkeypatch.setattr(financial, 'database_now', lambda _: datetime.now(timezone.utc))
    current = ok(client.get('/accounting/financial/status'))
    cutoff = historical.date().isoformat()
    command = {'closedThrough': cutoff, 'expectedVersion': current['version'], 'mutationId': 'close', 'reason': 'Period reviewed'}
    assert client.post('/accounting/financial/close-period', json=command).status_code == 409
    source = sources(client, 'receive')[0]
    ok(client.post('/accounting/sources/' + source['id'] + '/resolve', json={'date': cutoff, 'expectedVersion': 0, 'mutationId': 'historical-cost',
        'reason': 'Verified receipt cost', 'referenceId': 'invoice', 'lines': [{'accountCode': '1200', 'amountCents': 100, 'type': 'debit'}, {'accountCode': '2100', 'amountCents': 100, 'type': 'credit'}]}))
    command['expectedVersion'] = ok(client.get('/accounting/financial/status'))['version']
    closed = ok(client.post('/accounting/financial/close-period', json=command))
    assert closed['closedThrough'] == cutoff
    assert manual(client, key='backdate', date=cutoff)[0].status_code == 400
    assert ok(client.post('/accounting/financial/close-period', json=command)) == closed
    assert client.get('/accounting/income-statement?fromDate=2099-01-01').status_code == 422


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_serial_kit_count_and_transfer_postings_reconcile_under_each_cost_method(client, method):
    from tests.presentation.test_serials_kits_api import register, serial, define, kit
    for sku in ('PART', 'KIT', 'SERIAL'):
        product(client, sku)
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method}))
    ok(receive(client, 10, 101, sku='PART'))
    ok(define(client), 201)
    ok(activate(client))
    built, _ = kit(client, 'assemble', 2, 'assemble')
    ok(built)
    assert balances(client)['1220'] == 0 and balances(client)['1200'] == 1010
    undone, _ = kit(client, 'disassemble', 1, 'undo')
    ok(undone)
    sold, _ = kit(client, 'sale', 1, 'bundle-sale')
    ok(sold)
    assert balances(client)['5000'] == 202
    register(client)
    ok(serial(client, 'receive', 0, 'serial-receipt', unitCostCents=303))
    ok(serial(client, 'sell', 1, 'serial-sale'))
    ok(serial(client, 'return', 2, 'serial-return'))
    ok(serial(client, 'restock', 3, 'serial-restock'))
    ok(serial(client, 'transfer', 4, 'serial-transit', destinationId='branch'))
    assert balances(client)['1210'] == 303
    ok(serial(client, 'receive', 5, 'serial-arrival', locationId='branch'))
    ok(serial(client, 'write-off', 6, 'serial-loss'))
    assert balances(client)['5100'] == 303 and balances(client)['1210'] == 0
    stock = next(s for s in ok(client.get('/inventory')) if s['sku'] == 'PART' and s['locationId'] == 'shop')
    ok(client.post('/api/cycle-counts/adjust', json={'sku': 'PART', 'location_id': 'shop', 'delta': -1,
        'expected_version': stock['version'], 'reason_code': 'DAMAGE', 'reason': 'Damaged component', 'mutation_id': 'loss'}))
    assert balances(client)['1200'] == 707 and balances(client)['5100'] == 404
    stock = next(s for s in ok(client.get('/inventory')) if s['sku'] == 'PART' and s['locationId'] == 'shop')
    transfer = ok(client.post('/inventory/transfers', json={'sourceLocationId': 'shop', 'destinationLocationId': 'branch',
        'items': [{'sku': 'PART', 'quantity': 1}], 'mutationId': 'transfer'}), 201)
    dispatched = ok(client.post('/inventory/transfers/' + transfer['id'] + '/dispatch', json={'expectedVersion': 0,
        'mutationId': 'dispatch-transfer', 'reason': 'Move component', 'sourceVersions': {'PART': stock['version']}}))
    assert balances(client)['1210'] == 101
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']
    ok(client.post('/inventory/transfers/' + transfer['id'] + '/cancel', json={'expectedVersion': dispatched['version'],
        'mutationId': 'return-transfer', 'reason': 'Shipment returned'}))
    assert balances(client)['1210'] == 0
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']


async def test_customer_return_scrap_and_writeoff_reverse_cost_without_fabricating_refunds(client):
    from tests.presentation.test_returns_api import create, command, receive as return_receive
    product(client)
    ok(activate(client))
    ok(receive(client, 2, 101))
    sale = ok(client.post('/inventory/dispatch', json={'sku': 'ITEM', 'locationId': 'shop', 'quantity': 2, 'mutationId': 'sale'}))
    movement = next(m for m in ok(client.get('/inventory/movements')) if m['mutationId'] == 'sale')
    row = create(client, movement['id'], sale['consumedLayers'], 'SCRAP')
    authorized = ok(command(client, row, 'authorize'))
    ok(return_receive(client, authorized))
    values = balances(client)
    assert values['1200'] == 0 and values['5000'] == 0 and values['5100'] == 202
    assert values['1000'] == 0 and values['4000'] == 0
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']


async def test_financial_role_privacy_tenant_ownership_and_actor_bound_replay(client):
    from tests.presentation.test_tenant_security import staff, other_store
    ok(activate(client))
    response, payload = manual(client)
    journal = ok(response, 201)
    _, operator = staff(client, 'WAREHOUSE_OPERATOR', 'operator@example.com')
    _, viewer = staff(client, 'VIEWER', 'viewer@example.com')
    _, accountant = staff(client, 'ACCOUNTANT', 'accountant@example.com')
    assert client.get('/accounting/ledger', headers=operator).status_code == 403
    assert client.get('/accounting/trial-balance', headers=viewer).status_code == 403
    assert client.post('/accounting/journals', headers=operator, json=payload).status_code == 403
    assert client.post('/accounting/journals', headers=accountant, json=payload).status_code == 409
    assert client.get('/accounting/ledger', headers=accountant).status_code == 200
    _, foreign = other_store(client)
    assert client.get('/accounting/journals/' + journal['id'], headers=foreign).status_code == 404
    assert client.get('/accounting/ledger?tenantId=other').status_code == 403
    assert client.post('/accounting/journals', json={**payload, 'tenantId': 'other'}).status_code == 403
    assert client.get('/accounting/ledger', headers={'Authorization': ''}).status_code == 401


@pytest.mark.parametrize('method', ['ACCRUAL', 'CASH'])
async def test_confirmed_customer_credits_and_partial_cash_refunds_follow_return_sources(client, method):
    from tests.presentation.test_returns_api import create, command, receive as return_receive
    product(client)
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'FIFO', 'accountingMethod': method}))
    ok(activate(client))
    ok(receive(client, 4, 100))
    sale = ok(client.post('/inventory/dispatch', json={'sku': 'ITEM', 'locationId': 'shop', 'quantity': 2, 'mutationId': 'sale'}))
    sale_source = sources(client, 'dispatch')[0]
    document = ok(client.post('/accounting/documents', json={'kind': 'SALE', 'date': today(), 'reference': 'sale-doc', 'counterparty': 'Buyer',
        'amountCents': 400, 'sourceIds': [sale_source['id']], 'mutationId': 'sale-doc', 'reason': 'Actual sale price'}), 201)
    ok(client.post('/accounting/documents/' + document['id'] + '/settle', json={'date': today(), 'expectedVersion': 0, 'amountCents': 400,
        'reference': 'sale-payment', 'mutationId': 'sale-payment', 'reason': 'Actual payment received'}))
    rma = create(client, sale_source['id'], [{**sale['consumedLayers'][0], 'quantity': 1}], 'RESTOCK')
    rma = ok(command(client, rma, 'authorize'))
    ok(return_receive(client, rma))
    returned = sources(client, 'customer-return')[0]
    credit = {'date': today(), 'expectedVersion': 1, 'amountCents': 150, 'reference': 'return-credit',
        'sourceId': returned['id'], 'mutationId': 'credit', 'reason': 'Confirmed customer credit'}
    credited = ok(client.post('/accounting/documents/' + document['id'] + '/credit', json=credit))
    assert credited['refundDueCents'] == 150 and balances(client)['1000'] == 400
    assert balances(client)['4100'] == (150 if method == 'ACCRUAL' else 0)
    refund = {'date': today(), 'expectedVersion': 2, 'amountCents': 100, 'reference': 'cash-refund', 'mutationId': 'cash-refund', 'reason': 'Confirmed bank refund paid'}
    refunded = ok(client.post('/accounting/documents/' + document['id'] + '/refund', json=refund))
    assert refunded['refundDueCents'] == 50 and balances(client)['1000'] == 300
    assert ok(client.post('/accounting/documents/' + document['id'] + '/refund', json=refund)) == refunded
    assert client.post('/accounting/documents/' + document['id'] + '/refund', json={**refund, 'expectedVersion': 3, 'amountCents': 51,
        'reference': 'over-refund', 'mutationId': 'over-refund'}).status_code == 409
    completed = ok(client.post('/accounting/documents/' + document['id'] + '/refund', json={**refund, 'expectedVersion': 3, 'amountCents': 50,
        'reference': 'last-refund', 'mutationId': 'last-refund'}))
    assert completed['status'] == 'SETTLED' and completed['outstandingCents'] == completed['refundDueCents'] == 0
    assert balances(client)['1000'] == 250 and balances(client)['4100'] == 150
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']


@pytest.mark.parametrize('method', ['ACCRUAL', 'CASH'])
async def test_po_matched_invoice_supplier_credit_and_actual_vendor_refund(client, method):
    from tests.presentation.test_returns_api import create, command
    product(client)
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'FIFO', 'accountingMethod': method}))
    ok(activate(client))
    supplier = ok(client.post('/suppliers', json={'name': 'Vendor', 'mutationId': 'vendor'}), 201)
    other = ok(client.post('/suppliers', json={'name': 'Other vendor', 'mutationId': 'other-vendor'}), 201)
    order = ok(client.post('/purchase-orders', json={'supplierId': supplier['id'], 'locationId': 'shop', 'items': [
        {'sku': 'ITEM', 'quantity': 2, 'unitCostCents': 100}], 'mutationId': 'po'}), 201)
    for step in ('approve', 'send'):
        ok(client.post('/purchase-orders/' + order['id'] + '/' + step, json={'mutationId': step}))
    received = ok(client.post('/purchase-orders/' + order['id'] + '/receive', json={'receiptId': 'delivery', 'items': [{'sku': 'ITEM', 'quantity': 2}]}))
    source = sources(client, 'receive')[0]
    invoice = {'kind': 'PURCHASE', 'date': today(), 'reference': 'invoice', 'counterparty': 'Vendor', 'supplierId': supplier['id'],
        'amountCents': 200, 'sourceIds': [source['id']], 'mutationId': 'invoice', 'reason': 'Matched actual invoice'}
    assert client.post('/accounting/documents', json={**invoice, 'supplierId': other['id'], 'mutationId': 'wrong-vendor'}).status_code == 409
    document = ok(client.post('/accounting/documents', json=invoice), 201)
    ok(client.post('/accounting/documents/' + document['id'] + '/settle', json={'date': today(), 'expectedVersion': 0, 'amountCents': 200,
        'reference': 'vendor-payment', 'mutationId': 'vendor-payment', 'reason': 'Actual payment sent'}))
    layer = received['stockResults'][0]['costLayerId']
    rma = create(client, source['id'], [{'layerId': layer, 'quantity': 1}], None, kind='SUPPLIER')
    rma = ok(command(client, rma, 'authorize'))
    ok(client.post('/returns/rmas/' + rma['id'] + '/ship', json={'shipmentId': 'vendor-return', 'expectedVersion': rma['version'],
        'items': [{'layerId': layer, 'quantity': 1}]}))
    claim = sources(client, 'supplier-return')[0]
    assert balances(client)['1300'] == 100
    credited = ok(client.post('/accounting/documents/' + document['id'] + '/credit', json={'date': today(), 'expectedVersion': 1,
        'amountCents': 100, 'reference': 'vendor-credit', 'sourceId': claim['id'], 'mutationId': 'vendor-credit', 'reason': 'Vendor confirmed credit'}))
    assert credited['refundDueCents'] == 100 and balances(client)['1000'] == -200 and balances(client)['1300'] == 0
    refunded = ok(client.post('/accounting/documents/' + document['id'] + '/refund', json={'date': today(), 'expectedVersion': 2,
        'amountCents': 100, 'reference': 'vendor-refund', 'mutationId': 'vendor-refund', 'reason': 'Actual refund received'}))
    assert refunded['status'] == 'SETTLED' and balances(client)['1000'] == -100
    assert balances(client)['2000'] == balances(client)['2100'] == 0
    assert ok(client.get('/accounting/financial/reconciliation'))['isReconciled']


async def test_serial_return_credit_links_the_identified_sale_without_guessing_its_retail_price(client):
    from tests.presentation.test_serials_kits_api import register, serial
    product(client, 'SERIAL')
    ok(activate(client))
    register(client)
    ok(serial(client, 'receive', 0, 'receive', unitCostCents=123))
    ok(serial(client, 'sell', 1, 'sale'))
    sold = sources(client, 'serial-sell')[0]
    document = ok(client.post('/accounting/documents', json={'kind': 'SALE', 'date': today(), 'reference': 'serial-sale', 'counterparty': 'Buyer',
        'amountCents': 350, 'sourceIds': [sold['id']], 'mutationId': 'serial-sale-doc', 'reason': 'Actual serial sale price'}), 201)
    ok(serial(client, 'return', 2, 'return'))
    returned = sources(client, 'serial-return')[0]
    assert returned['facts']['result']['sourceMovementId'] == sold['id']
    credited = ok(client.post('/accounting/documents/' + document['id'] + '/credit', json={'date': today(), 'expectedVersion': 0,
        'amountCents': 350, 'reference': 'serial-credit', 'sourceId': returned['id'], 'mutationId': 'serial-credit', 'reason': 'Confirmed actual credit'}))
    assert credited['status'] == 'SETTLED' and credited['refundDueCents'] == 0
    assert balances(client)['4100'] == 350 and balances(client)['5000'] == 0


async def test_manual_retail_expenses_and_capital_feed_income_and_balance_sheet(client):
    ok(activate(client))
    ok(manual(client, lines=[{'accountCode': '1000', 'amountCents': 100000, 'type': 'debit'},
        {'accountCode': '3100', 'amountCents': 100000, 'type': 'credit'}])[0], 201)
    ok(manual(client, key='rent', description='Actual rent payment', lines=[{'accountCode': '6200', 'amountCents': 25000, 'type': 'debit'},
        {'accountCode': '1000', 'amountCents': 25000, 'type': 'credit'}])[0], 201)
    assert ok(client.get('/accounting/income-statement'))['expenseCents'] == 25000
    assert ok(client.get('/accounting/income-statement'))['profitCents'] == -25000
    sheet = ok(client.get('/accounting/balance-sheet'))
    assert sheet['assetsCents'] == 75000 and sheet['equityCents'] == 100000 and sheet['currentEarningsCents'] == -25000 and sheet['isBalanced']
