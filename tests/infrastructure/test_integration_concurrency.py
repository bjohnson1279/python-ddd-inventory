import asyncio
import base64
import hashlib
import hmac
import json
import pytest
import httpx
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from tests.infrastructure.test_outbox_contract import outbox_database
from src.infrastructure.integration_models import IntegrationOrder as Order, IntegrationDelivery as Delivery
from src.application.integration_service import dispatch_head
from src.application.workers.outbox_worker import set_worker_tenant


@pytest.mark.asyncio
async def test_signed_order_and_delivery_concurrency_with_owned_parents(outbox_database, monkeypatch):
    client, sessions, runtime, backend = outbox_database
    keys = {'store-a': 'INTEGRATION_A_SECRET', 'store-b': 'INTEGRATION_B_SECRET'}
    monkeypatch.setenv('INTEGRATION_A_SECRET', 'a' * 40)
    monkeypatch.setenv('INTEGRATION_B_SECRET', 'b' * 40)
    monkeypatch.setenv('INTEGRATION_SINK_SECRET', 's' * 40)
    monkeypatch.setenv('INTEGRATION_ENDPOINT', 'https://connector.example.com/events')
    monkeypatch.setenv('INTEGRATION_TENANT_SETTINGS', json.dumps({'store-a': [keys['store-a'], 'INTEGRATION_SINK_SECRET', 'INTEGRATION_ENDPOINT'], 'store-b': [keys['store-b']]}))
    async def enroll(tenant):
        assert (await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
            'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})).status_code == 201
        login = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        response = await client.post('/integrations/connections', headers=headers, json={'name': tenant, 'provider': 'SHOPIFY',
            'origin': tenant + '.myshopify.com', 'secretRef': keys[tenant], 'mutationId': 'connect', 'reason': 'Connect store'})
        assert response.status_code == 201, response.text
        assert (await client.post('/products', headers=headers, json={'sku': 'A', 'name': 'A', 'price_amount': '5.00'})).status_code == 201
        assert (await client.post('/inventory/receive', headers=headers, json={'sku': 'A', 'quantity': 3, 'unitCostCents': 100})).status_code == 200
        return headers, response.json()
    a, ca = await enroll('store-a')
    b, cb = await enroll('store-b')
    sink = await client.post('/integrations/connections', headers=a, json={'name': 'Ledger destination', 'provider': 'EVENT_SINK',
        'secretRef': 'INTEGRATION_SINK_SECRET', 'endpointRef': 'INTEGRATION_ENDPOINT', 'topics': ['StockChanged'], 'mutationId': 'sink', 'reason': 'Export inventory'})
    assert sink.status_code == 201, sink.text
    sink_id = sink.json()['id']
    raw = json.dumps({'id': 'same-order', 'line_items': [{'sku': 'A', 'quantity': 2}]}).encode()
    wh = {'X-Shopify-Hmac-Sha256': base64.b64encode(hmac.new(('a' * 40).encode(), raw, hashlib.sha256).digest()).decode(),
        'X-Shopify-Shop-Domain': ca['origin'], 'X-Shopify-Topic': 'orders/create', 'X-Shopify-Webhook-Id': 'delivery-1'}
    responses = await asyncio.gather(*(client.post(ca['webhookPath'], content=raw, headers=wh) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    stock = (await client.get('/inventory', headers=a)).json()[0]
    assert stock['quantity'] == 3 and stock['allocated'] == 2 and stock['available'] == 1
    assert (await client.get('/inventory', headers=b)).json()[0]['quantity'] == 3
    assert (await client.post(cb['webhookPath'], content=raw, headers=wh)).status_code == 401
    assert (await client.get('/integrations/orders', headers=b)).json() == []
    async def last_unit(identifier):
        body = json.dumps({'id': identifier, 'line_items': [{'sku': 'A', 'quantity': 1}]}).encode()
        headers = {**wh, 'X-Shopify-Webhook-Id': identifier,
            'X-Shopify-Hmac-Sha256': base64.b64encode(hmac.new(('a' * 40).encode(), body, hashlib.sha256).digest()).decode()}
        return await client.post(ca['webhookPath'], content=body, headers=headers)
    competitors = await asyncio.gather(last_unit('last-1'), last_unit('last-2'))
    assert sorted(response.status_code for response in competitors) == [200, 400]
    assert (await client.get('/inventory', headers=a)).json()[0]['available'] == 0
    assert (await client.post('/integrations/connections/' + ca['id'] + '/active', headers=b, json={
        'active': False, 'expectedVersion': 0, 'mutationId': 'foreign', 'reason': 'Must fail'})).status_code == 404
    entered, release, requests = asyncio.Event(), asyncio.Event(), []
    async def remote(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={'accepted': True, 'eventId': json.loads(request.content)['eventId'], 'externalId': 'remote-1'})
    async def send():
        async with sessions() as session:
            if backend == 'postgres':
                await set_worker_tenant(session, 'store-a')
            return await dispatch_head(session, 'store-a', sink_id, transport=httpx.MockTransport(remote))
    first = asyncio.create_task(send())
    await asyncio.wait_for(entered.wait(), 30)
    try:
        assert await send() is None
    finally:
        release.set()
        result = await first
    assert result['status'] == 'DELIVERED' and len(requests) == 1
    async with sessions() as session:
        if backend == 'postgres':
            await set_worker_tenant(session, 'store-a')
        assert await session.scalar(select(func.count()).select_from(Order)) == 2
        row = await session.scalar(select(Delivery).where(Delivery.tenant_id == 'store-a'))
        assert row.status == 'DELIVERED'
    if backend == 'postgres':
        async with runtime.begin() as conn:
            for table in ('python_integration_connections', 'python_integration_orders', 'python_integration_receipts', 'python_integration_deliveries'):
                assert await conn.scalar(text('SELECT count(*) FROM ' + table)) == 0
                assert await conn.scalar(text("SELECT relforcerowsecurity FROM pg_class WHERE oid='" + table + "'::regclass"))
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id','store-b',true)"))
                await conn.execute(text("INSERT INTO python_integration_orders (id,tenant_id,connection_id,external_id,delivery_id,request,result,status,version,created_at) VALUES ('injected','store-b',:parent,'x','y','{}','{}','RESERVED',0,now())"), {'parent': ca['id']})
        assert denied.value.orig.sqlstate == '42501'
