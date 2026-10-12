import base64
import hashlib
import hmac
import json
import pytest
import httpx
from sqlalchemy import select, func, update
from src.infrastructure.integration_models import IntegrationConnection as Connection, IntegrationOrder as Order, IntegrationDelivery as Delivery
from src.infrastructure.stock_models import StockMovementModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.application.integration_service import dispatch_head

SECRET = 'test-signing-secret-32-characters-only'


def connect(client, monkeypatch, provider='SHOPIFY', key='INTEGRATION_SHOP_SECRET', **extra):
    monkeypatch.setenv(key, SECRET)
    monkeypatch.setenv('INTEGRATION_TENANT_SETTINGS', json.dumps({'tenant-1': [key, 'INTEGRATION_ENDPOINT']}))
    payload = {'provider': provider, 'name': 'Retail store', 'secretRef': key, 'locationId': 'default',
        'origin': 'store.myshopify.com' if provider == 'SHOPIFY' else 'https://store.example.com/',
        'mutationId': key, 'reason': 'Register owned integration', **extra}
    response = client.post('/integrations/connections', json=payload)
    assert response.status_code == 201, response.text
    assert SECRET not in response.text and key not in response.text
    assert client.post('/integrations/connections', json=payload).json() == response.json()
    return response.json()


def receipt(client, sku='A', qty=5):
    assert client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '4.00'}).status_code == 201
    response = client.post('/inventory/receive', json={'sku': sku, 'quantity': qty, 'unitCostCents': 125})
    assert response.status_code == 200, response.text


def webhook(client, connection, items=None, order='order-1', delivery='delivery-1', signature=None, **payload):
    raw = json.dumps({'id': order, 'line_items': items or [{'sku': 'A', 'quantity': 2}], **payload}).encode()
    headers = {'X-Shopify-Hmac-Sha256': signature or base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode(),
        'X-Shopify-Topic': 'orders/create', 'X-Shopify-Shop-Domain': connection['origin'], 'X-Shopify-Webhook-Id': delivery}
    if connection['provider'] == 'WOOCOMMERCE':
        headers = {'X-WC-Webhook-Signature': headers['X-Shopify-Hmac-Sha256'], 'X-WC-Webhook-Topic': 'order.created',
            'X-WC-Webhook-Source': connection['origin'], 'X-WC-Webhook-Delivery-Id': delivery}
    return client.post(connection['webhookPath'], content=raw, headers=headers)


@pytest.mark.parametrize('provider', ['SHOPIFY', 'WOOCOMMERCE'])
async def test_signed_orders_replay_exactly_once_and_persist(client, db_session, monkeypatch, provider):
    connection = connect(client, monkeypatch, provider)
    receipt(client)
    first = webhook(client, connection)
    assert first.status_code == 200, first.text
    assert webhook(client, connection, delivery='another-delivery').json() == first.json()
    assert webhook(client, connection, delivery='another-delivery', order='changed-order').status_code == 409
    stock = client.get('/inventory').json()[0]
    assert stock['quantity'] == 5 and stock['allocated'] == 2 and stock['available'] == 3
    assert await db_session.scalar(select(func.count()).select_from(Order)) == 1
    assert 'cost' not in first.text.lower()
    assert client.get('/integrations/orders').json() == [first.json()]
    assert webhook(client, connection, items=[{'sku': 'A', 'quantity': 1}]).status_code == 409
    command = {'expectedVersion': 0, 'mutationId': 'ship-once', 'reason': 'Confirmed physical shipment'}
    shipped = client.post('/integrations/orders/' + first.json()['id'] + '/fulfill', json=command)
    assert shipped.status_code == 200 and shipped.json()['status'] == 'FULFILLED'
    assert client.post('/integrations/orders/' + first.json()['id'] + '/fulfill', json=command).json() == shipped.json()
    stock = client.get('/inventory').json()[0]
    assert stock['quantity'] == 3 and stock['allocated'] == 0
    assert webhook(client, connection).json() == first.json()


async def test_order_failure_rolls_back_all_stock_cost_events_and_receipt(client, db_session, monkeypatch):
    connection = connect(client, monkeypatch)
    receipt(client)
    before = await db_session.scalar(select(func.count()).select_from(OutboxEventModel))
    failed = webhook(client, connection, items=[{'sku': 'A', 'quantity': 2}, {'sku': 'Z-MISSING', 'quantity': 1}])
    assert failed.status_code == 400
    assert client.get('/inventory').json()[0]['quantity'] == 5
    assert await db_session.scalar(select(func.count()).select_from(Order)) == 0
    assert await db_session.scalar(select(func.count()).select_from(OutboxEventModel)) == before
    assert webhook(client, connection).status_code == 200


@pytest.mark.parametrize('quantity', [0, -1, True, 1.5])
def test_order_invalid_quantities_are_rejected(client, monkeypatch, quantity):
    connection = connect(client, monkeypatch)
    receipt(client)
    assert webhook(client, connection, items=[{'sku': 'A', 'quantity': quantity}]).status_code == 400
    assert client.get('/inventory').json()[0]['quantity'] == 5


def test_signature_tenant_origin_activation_and_unknown_skus(client, monkeypatch):
    connection = connect(client, monkeypatch)
    receipt(client)
    assert webhook(client, connection, signature='bad').status_code == 401
    assert webhook(client, {**connection, 'origin': 'evil.myshopify.com'}).status_code == 401
    assert webhook(client, connection, tenantId='foreign').status_code == 403
    assert webhook(client, connection, items=[{'quantity': 1}]).status_code == 400
    assert client.post('/integrations/connections/' + connection['id'] + '/active', json={
        'active': False, 'expectedVersion': 0, 'mutationId': 'disable', 'reason': 'Disconnect store'}).status_code == 200
    assert webhook(client, connection).status_code == 401
    assert client.get('/inventory').json()[0]['quantity'] == 5


async def test_real_destination_ack_retry_ordering_and_snapshots(client, db_session, monkeypatch):
    monkeypatch.setenv('INTEGRATION_ENDPOINT', 'https://connector.example.com/events')
    sink = connect(client, monkeypatch, 'EVENT_SINK', key='INTEGRATION_SINK_SECRET', origin=None,
        endpointRef='INTEGRATION_ENDPOINT', topics=['StockChanged', 'JournalEntryPosted'])
    assert client.post('/accounting/financial/activate', json={'mutationId': 'open', 'expectedVersion': 0, 'reason': 'Open books'}).status_code == 200
    receipt(client)
    rows = list(await db_session.scalars(select(Delivery).order_by(Delivery.number)))
    assert {row.payload['type'] for row in rows} == {'JournalEntryPosted', 'StockChanged'}
    assert [row.number for row in rows] == [1, 2]
    saved = rows[0].payload
    requests = []
    def failure(request):
        requests.append(request)
        return httpx.Response(503, text=SECRET)
    for attempt in range(3):
        await db_session.execute(update(Delivery).values(next_attempt_at=None))
        await db_session.commit()
        result = await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(failure))
        assert result['status'] == ('FAILED' if attempt == 2 else 'PENDING')
        assert result['lastError'] == 'RemoteHTTP503' and SECRET not in str(result)
    assert await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(failure)) is None
    retry = {'mutationId': 'retry-head', 'expectedVersion': result['version'], 'reason': 'Connector restored'}
    retried = client.post('/integrations/deliveries/' + result['id'] + '/retry', json=retry)
    assert retried.status_code == 200, retried.text
    assert client.post('/integrations/deliveries/' + result['id'] + '/retry', json=retry).json() == retried.json()
    def accepted(request):
        requests.append(request)
        assert hmac.compare_digest(request.headers['X-Inventory-Signature'], hmac.new(SECRET.encode(), request.content, hashlib.sha256).hexdigest())
        return httpx.Response(200, json={'accepted': True, 'eventId': json.loads(request.content)['eventId'], 'externalId': 'real-remote-record'})
    delivered = await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(accepted))
    assert delivered['status'] == 'DELIVERED'
    assert json.loads(requests[-1].content) == saved
    assert len({request.headers['Idempotency-Key'] for request in requests}) == 1
    assert (await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(accepted)))['number'] == 2


async def test_http_success_without_acknowledgement_is_not_delivery(client, db_session, monkeypatch):
    monkeypatch.setenv('INTEGRATION_ENDPOINT', 'https://connector.example.com/events')
    sink = connect(client, monkeypatch, 'EVENT_SINK', key='INTEGRATION_SINK_SECRET', origin=None,
        endpointRef='INTEGRATION_ENDPOINT', topics=['StockChanged'])
    receipt(client)
    response = await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'success': True})))
    assert response['status'] == 'PENDING' and response['lastError'] == 'InvalidAcknowledgement'


def test_connection_missing_settings_and_no_mock_external_connect(client, monkeypatch):
    monkeypatch.delenv('INTEGRATION_MISSING', raising=False)
    assert client.post('/integrations/connections', json={'provider': 'SHOPIFY', 'name': 'Missing', 'secretRef': 'INTEGRATION_MISSING',
        'origin': 'store.myshopify.com', 'mutationId': 'missing', 'reason': 'Setup'}).status_code == 400
    assert client.post('/integrations/amazon/connect', json={}).status_code == 501
    assert client.post('/integrations/woocommerce/connect', json={}).status_code == 501


async def test_cancellation_releases_reservation_without_cogs_and_replays(client, db_session, monkeypatch):
    connection = connect(client, monkeypatch)
    receipt(client)
    assert client.post('/accounting/financial/activate', json={'expectedVersion': 0, 'mutationId': 'activate', 'reason': 'Open ledger'}).status_code == 200
    order = webhook(client, connection).json()
    assert len(client.get('/accounting/journals').json()) == 1
    payload = {'expectedVersion': 0, 'mutationId': 'cancel', 'reason': 'Customer cancelled before shipment'}
    path = '/integrations/orders/' + order['id'] + '/cancel'
    cancelled = client.post(path, json=payload)
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'CANCELLED'
    assert client.post(path, json=payload).json() == cancelled.json()
    assert client.post('/integrations/orders/' + order['id'] + '/fulfill', json={**payload, 'mutationId': 'ship-cancelled'}).status_code == 409
    stock = client.get('/inventory').json()[0]
    assert stock['quantity'] == 5 and stock['allocated'] == 0
    assert len(client.get('/accounting/journals').json()) == 1
    assert client.get('/integrations/orders').json()[0]['status'] == 'CANCELLED'


def test_operator_setting_assignments_and_integration_role_privacy(client, monkeypatch):
    monkeypatch.setenv('INTEGRATION_FOREIGN_KEY', 'foreign-secret-that-is-at-least-32-characters')
    monkeypatch.setenv('INTEGRATION_TENANT_SETTINGS', json.dumps({'foreign': ['INTEGRATION_FOREIGN_KEY']}))
    response = client.post('/integrations/connections', json={'name': 'Disallowed', 'provider': 'SHOPIFY', 'origin': 'evil.myshopify.com',
        'secretRef': 'INTEGRATION_FOREIGN_KEY', 'mutationId': 'foreign-key', 'reason': 'Must not select other tenant settings'})
    assert response.status_code == 400
    assert client.post('/users', json={'email': 'viewer@example.com', 'role': 'VIEWER', 'password': 'viewer-password'}).status_code == 201
    token = client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'viewer@example.com', 'password': 'viewer-password'}).json()['token']
    headers = {'Authorization': 'Bearer ' + token}
    for path in ('/integrations/connections', '/integrations/orders', '/integrations/deliveries'):
        assert client.get(path, headers=headers).status_code == 403


async def test_woocommerce_activation_ping_acknowledges_reachability_only(client, db_session, monkeypatch):
    connection = connect(client, monkeypatch, 'WOOCOMMERCE')
    receipt(client)
    ping = client.post(connection['webhookPath'], content=b'webhook_id=123', headers={'Content-Type': 'application/x-www-form-urlencoded'})
    assert ping.status_code == 200 and ping.json() == {'status': 'PING_ONLY'}
    assert await db_session.scalar(select(func.count()).select_from(Order)) == 0
    stock = client.get('/inventory').json()[0]
    assert stock['quantity'] == 5 and stock['allocated'] == 0
    malformed = client.post(connection['webhookPath'], content=b'webhook_id=123&line_items=unsafe', headers={'Content-Type': 'application/x-www-form-urlencoded'})
    assert malformed.status_code == 401


@pytest.mark.parametrize('method', ['CASH', 'ACCRUAL'])
async def test_shipment_posts_actual_cogs_without_inferring_customer_payment(client, monkeypatch, method):
    assert client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'FIFO', 'accountingMethod': method}).status_code == 200
    receipt(client)
    assert client.post('/accounting/financial/activate', json={'expectedVersion': 0, 'mutationId': 'open', 'reason': 'Reviewed opening'}).status_code == 200
    connection = connect(client, monkeypatch)
    order = webhook(client, connection).json()
    assert len(client.get('/accounting/journals').json()) == 1
    shipped = client.post('/integrations/orders/' + order['id'] + '/fulfill', json={
        'expectedVersion': 0, 'mutationId': 'ship', 'reason': 'Physical shipment confirmed'})
    assert shipped.status_code == 200, shipped.text
    balances = {row['accountCode']: row['balanceCents'] for row in client.get('/accounting/trial-balance').json()['accounts']}
    assert balances['1200'] == 375 and balances['5000'] == 250
    assert balances['4000'] == 0 and balances['1000'] == 0


async def test_acknowledged_but_uncommitted_delivery_reuses_the_remote_idempotency_key(client, db_session, monkeypatch):
    monkeypatch.setenv('INTEGRATION_ENDPOINT', 'https://connector.example.com/events')
    sink = connect(client, monkeypatch, 'EVENT_SINK', key='INTEGRATION_SINK_SECRET', origin=None,
        endpointRef='INTEGRATION_ENDPOINT', topics=['StockChanged'])
    receipt(client)
    accepted = {}
    requests = []
    def remote(request):
        requests.append(request)
        key = request.headers['Idempotency-Key']
        accepted.setdefault(key, {'eventId': json.loads(request.content)['eventId'], 'accepted': True, 'externalId': 'remote-once'})
        return httpx.Response(200, json=accepted[key])
    original_commit = db_session.commit
    calls = 0
    async def fail_ack_commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Simulate database failure after remote acknowledgement')
        await original_commit()
    monkeypatch.setattr(db_session, 'commit', fail_ack_commit)
    with pytest.raises(RuntimeError):
        await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(remote))
    await db_session.rollback()
    monkeypatch.setattr(db_session, 'commit', original_commit)
    from datetime import datetime, timezone, timedelta
    await db_session.execute(update(Delivery).values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db_session.commit()
    result = await dispatch_head(db_session, 'tenant-1', sink['id'], transport=httpx.MockTransport(remote))
    assert result['status'] == 'DELIVERED' and result['attempts'] == 2
    assert len(requests) == 2 and len(accepted) == 1
    assert requests[0].content == requests[1].content
