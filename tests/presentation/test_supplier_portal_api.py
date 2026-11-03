import pytest
from httpx import AsyncClient, ASGITransport
import pytest_asyncio
from src.presentation.main import app
from datetime import datetime, timedelta

@pytest_asyncio.fixture
async def async_client(): 
    # Notice we don't mock the DB or auth closely here, just asserting routing & payload structure
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        yield ac

@pytest.mark.asyncio
async def test_list_supplier_pos(async_client):
    response = await async_client.get("/api/supplier-portal/pos")
    
    # Expect 401 Unauthorized because test client does not have SUPPLIER_USER role automatically in this stub
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_supplier_asn_persists_order_and_notice_once(client, db_session):
    from tests.presentation.test_procurement_api import setup, sent, ok
    from src.infrastructure.procurement_models import ASNModel, PurchaseOrderModel
    from sqlalchemy import select, func
    supplier, order = setup(client)
    sent(client, order)
    user = ok(client.post('/users', json={'email': 'supplier-merge@example.com', 'role': 'SUPPLIER_USER',
        'supplierId': supplier['id'], 'password': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    ok(client.post('/api/supplier-portal/pos/'+order['id']+'/acknowledge', headers=headers,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-merge'}))
    body = {'po_id': order['id'], 'tracking_number': 'TRACK', 'estimated_delivery_date': '2030-01-01T00:00:00Z',
        'items': [{'sku': 'A', 'shipped_quantity': 10}], 'mutationId': 'asn-merge'}
    first = ok(client.post('/api/supplier-portal/asns', headers=headers, json=body))
    assert ok(client.post('/api/supplier-portal/asns', headers=headers, json=body)) == first
    assert await db_session.scalar(select(func.count()).select_from(ASNModel)) == 1
    row = await db_session.get(PurchaseOrderModel, order['id'])
    assert row.status == 'SHIPPED'

@pytest.mark.asyncio
async def test_acknowledge_po_no_auth(async_client):
    response = await async_client.post(
        "/api/supplier-portal/pos/po-123/acknowledge", 
        json={"expected_delivery_date": datetime.utcnow().isoformat()}
    )
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_submit_asn_no_auth(async_client):
    response = await async_client.post(
        "/api/supplier-portal/asns", 
        json={
            "po_id": "po-123",
            "tracking_number": "TRK",
            "estimated_delivery_date": datetime.utcnow().isoformat(),
            "items": [{"sku": "SKU1", "shipped_quantity": 100}]
        }
    )
    assert response.status_code == 401


def create_supplier(client, name):
    from tests.presentation.test_procurement_api import ok
    import uuid
    return ok(client.post('/suppliers', json={'name': name, 'leadTimeDays': 7,
                                              'mutationId': str(uuid.uuid4())}), 201)

def create_po(client, supplier_id, skus=('A',), **fields):
    from tests.presentation.test_procurement_api import ok
    import uuid
    for sku in skus:
        client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '9.00'})
    return ok(client.post('/purchase-orders', json={'supplierId': supplier_id,
        'items': [{'sku': sku, 'quantity': 10, 'unitCostCents': 100} for sku in skus],
        'mutationId': str(uuid.uuid4()), **fields}), 201)

def create_supplier_user(client, email, supplier_id):
    from tests.presentation.test_procurement_api import ok
    user = ok(client.post('/users', json={
        'email': email,
        'role': 'SUPPLIER_USER',
        'supplierId': supplier_id,
        'password': 'test-password'
    }), 201)
    token = ok(client.post('/auth/login', json={
        'tenantId': 'tenant-1',
        'email': user['email'],
        'password': 'test-password'
    }))['token']
    return {'Authorization': f'Bearer {token}'}


@pytest.mark.asyncio
async def test_supplier_portal_access_without_supplier_binding(client, db_session):
    from tests.presentation.test_procurement_api import ok
    from src.infrastructure.auth.identity import UserModel
    # Create a regular user then manually grant SUPPLIER_USER role in DB without supplier_id
    user = ok(client.post('/users', json={
        'email': 'unbound-supplier@example.com',
        'role': 'USER',
        'password': 'test-password'
    }), 201)
    db_user = await db_session.get(UserModel, user['id'])
    db_user.role = 'SUPPLIER_USER'
    db_user.supplier_id = None
    await db_session.commit()

    token = ok(client.post('/auth/login', json={
        'tenantId': 'tenant-1',
        'email': user['email'],
        'password': 'test-password'
    }))['token']
    headers = {'Authorization': f'Bearer {token}'}

    res = client.get('/api/supplier-portal/pos', headers=headers)
    assert res.status_code == 403
    assert 'Supplier identity is not bound to this user' in res.json()['detail']


@pytest.mark.asyncio
async def test_supplier_pos_pagination_and_isolation(client):
    from tests.presentation.test_procurement_api import sent, ok

    # Setup Supplier 1 and POs
    sup1 = create_supplier(client, 'Supplier-Pos-1')
    po1 = create_po(client, sup1['id'], skus=('SKU-P1',))
    po2 = create_po(client, sup1['id'], skus=('SKU-P1',))
    # po1 is sent (open), po2 is draft (not open)
    sent(client, po1)

    # Setup Supplier 2 and PO
    sup2 = create_supplier(client, 'Supplier-Pos-2')
    po3 = create_po(client, sup2['id'], skus=('SKU-P2',))
    sent(client, po3)

    # Create supplier user bound to Supplier 1
    headers1 = create_supplier_user(client, 'sup1-pos@example.com', sup1['id'])

    # Fetch POs for Supplier 1
    res = ok(client.get('/api/supplier-portal/pos', headers=headers1))
    pos = res['pos']
    po_ids = [p['id'] for p in pos]
    assert po1['id'] in po_ids
    assert po2['id'] not in po_ids
    assert po3['id'] not in po_ids

    # Test pagination parameters: limit=1, offset=0
    paged = ok(client.get('/api/supplier-portal/pos?limit=1&offset=0', headers=headers1))
    assert len(paged['pos']) == 1

    # Empty page via high offset
    paged_empty = ok(client.get('/api/supplier-portal/pos?limit=10&offset=100', headers=headers1))
    assert len(paged_empty['pos']) == 0


@pytest.mark.asyncio
async def test_acknowledge_po_conflict_and_not_found(client):
    from tests.presentation.test_procurement_api import sent, ok

    sup1 = create_supplier(client, 'Supplier-Ack-1')
    po1 = create_po(client, sup1['id'], skus=('SKU-ACK-1',))  # po1 is draft
    sup2 = create_supplier(client, 'Supplier-Ack-2')
    po2 = create_po(client, sup2['id'], skus=('SKU-ACK-2',))
    sent(client, po2)

    headers1 = create_supplier_user(client, 'sup1-ack@example.com', sup1['id'])

    # Draft PO is hidden from supplier visibility, so acknowledging po1 while still draft returns 404 Not Found
    res_draft = client.post(
        f'/api/supplier-portal/pos/{po1["id"]}/acknowledge',
        headers=headers1,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-draft'}
    )
    assert res_draft.status_code == 404

    # Acknowledging non-existent PO should return 404
    res_404 = client.post(
        '/api/supplier-portal/pos/non-existent-po/acknowledge',
        headers=headers1,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-404'}
    )
    assert res_404.status_code == 404

    # Acknowledging another supplier's PO (po2 belongs to sup2) should return 404
    res_other = client.post(
        f'/api/supplier-portal/pos/{po2["id"]}/acknowledge',
        headers=headers1,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-other'}
    )
    assert res_other.status_code == 404

    # Send po1 so it becomes visible to sup1 and can be acknowledged
    sent(client, po1)
    res_ack_ok = ok(client.post(
        f'/api/supplier-portal/pos/{po1["id"]}/acknowledge',
        headers=headers1,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-ok-1'}
    ))
    assert res_ack_ok['status'] == 'acknowledged'

    # Replaying same mutationId returns the same result (idempotency)
    res_replay = ok(client.post(
        f'/api/supplier-portal/pos/{po1["id"]}/acknowledge',
        headers=headers1,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-ok-1'}
    ))
    assert res_replay == res_ack_ok


@pytest.mark.asyncio
async def test_submit_asn_validation_and_errors(client):
    from tests.presentation.test_procurement_api import sent, ok

    sup = create_supplier(client, 'Supplier-Asn-Val')
    po = create_po(client, sup['id'], skus=('SKU-ASN-VAL',))
    sent(client, po)

    headers = create_supplier_user(client, 'sup-asn-val@example.com', sup['id'])

    # Duplicate SKU in items should return 400 Bad Request
    res_dup = client.post(
        '/api/supplier-portal/asns',
        headers=headers,
        json={
            'po_id': po['id'],
            'tracking_number': 'TRK-DUP',
            'estimated_delivery_date': '2030-01-01T00:00:00Z',
            'items': [
                {'sku': 'SKU-ASN-VAL', 'shipped_quantity': 2},
                {'sku': 'SKU-ASN-VAL', 'shipped_quantity': 3}
            ],
            'mutationId': 'asn-dup'
        }
    )
    assert res_dup.status_code == 400
    assert 'shipped SKU must occur once' in res_dup.json()['detail']

    # Over-shipping quantity > ordered (10 ordered, try shipping 15) should return 409 Conflict
    res_over = client.post(
        '/api/supplier-portal/asns',
        headers=headers,
        json={
            'po_id': po['id'],
            'tracking_number': 'TRK-OVER',
            'estimated_delivery_date': '2030-01-01T00:00:00Z',
            'items': [{'sku': 'SKU-ASN-VAL', 'shipped_quantity': 15}],
            'mutationId': 'asn-over'
        }
    )
    assert res_over.status_code == 409

    # Unknown PO should return 404
    res_404 = client.post(
        '/api/supplier-portal/asns',
        headers=headers,
        json={
            'po_id': 'unknown-po-id',
            'tracking_number': 'TRK-UNKNOWN',
            'estimated_delivery_date': '2030-01-01T00:00:00Z',
            'items': [{'sku': 'SKU-ASN-VAL', 'shipped_quantity': 5}],
            'mutationId': 'asn-unknown'
        }
    )
    assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_get_asns_listing_and_isolation(client):
    from tests.presentation.test_procurement_api import sent, ok

    # Setup Supplier 1 and ASN
    sup1 = create_supplier(client, 'Supplier-Asn-List-1')
    po1 = create_po(client, sup1['id'], skus=('SKU-ASN1',))
    sent(client, po1)
    headers1 = create_supplier_user(client, 'sup1-asn-list@example.com', sup1['id'])

    ok(client.post('/api/supplier-portal/asns', headers=headers1, json={
        'po_id': po1['id'],
        'tracking_number': 'TRK-1',
        'estimated_delivery_date': '2030-01-01T00:00:00Z',
        'items': [{'sku': 'SKU-ASN1', 'shipped_quantity': 5}],
        'mutationId': 'asn-1'
    }))

    # List ASNs for Supplier 1
    res1 = ok(client.get('/api/supplier-portal/asns', headers=headers1))
    assert len(res1['asns']) == 1
    assert res1['asns'][0]['po_id'] == po1['id']
    assert res1['asns'][0]['tracking_number'] == 'TRK-1'

    # Setup Supplier 2 with no ASNs
    sup2 = create_supplier(client, 'Supplier-Asn-List-2')
    po2 = create_po(client, sup2['id'], skus=('SKU-ASN2',))
    headers2 = create_supplier_user(client, 'sup2-asn-list@example.com', sup2['id'])

    res2 = ok(client.get('/api/supplier-portal/asns', headers=headers2))
    assert len(res2['asns']) == 0


@pytest.mark.asyncio
async def test_supplier_performance_metrics(client):
    from tests.presentation.test_procurement_api import sent, receive, ok

    # Setup supplier
    sup = create_supplier(client, 'Supplier-Perf')
    po1 = create_po(client, sup['id'], skus=('SKU-PERF-1',))
    headers = create_supplier_user(client, 'sup-perf@example.com', sup['id'])

    # Before any completed orders, metrics should report 0 completed orders and None otif_percentage
    perf_init = ok(client.get('/api/supplier-portal/performance', headers=headers))
    assert perf_init['performance']['completed_orders'] == 0
    assert perf_init['performance']['measured_orders'] == 0
    assert perf_init['performance']['otif_percentage'] is None
    assert perf_init['performance']['defect_rate_percentage'] is None

    # Complete po1: send, acknowledge with future delivery date, then receive in full
    sent(client, po1)
    ok(client.post(
        f'/api/supplier-portal/pos/{po1["id"]}/acknowledge',
        headers=headers,
        json={'expected_delivery_date': '2099-01-01T00:00:00Z', 'mutationId': 'ack-perf-1'}
    ))
    ok(receive(client, po1, quantity=10, receipt='rec-perf-1', items=[{'sku': 'SKU-PERF-1', 'quantity': 10}]))

    # Now performance should show 1 completed order, 1 measured order, 100% OTIF
    perf_after = ok(client.get('/api/supplier-portal/performance', headers=headers))
    assert perf_after['performance']['completed_orders'] == 1
    assert perf_after['performance']['measured_orders'] == 1
    assert perf_after['performance']['otif_percentage'] == 100.0

    # Add a second order that was received late (expected in the past)
    po2 = create_po(client, sup['id'], skus=('SKU-PERF-2',))
    sent(client, po2)
    ok(client.post(
        f'/api/supplier-portal/pos/{po2["id"]}/acknowledge',
        headers=headers,
        json={'expected_delivery_date': '2020-01-01T00:00:00Z', 'mutationId': 'ack-perf-2'}
    ))
    ok(receive(client, po2, quantity=10, receipt='rec-perf-2', items=[{'sku': 'SKU-PERF-2', 'quantity': 10}]))

    perf_late = ok(client.get('/api/supplier-portal/performance', headers=headers))
    assert perf_late['performance']['completed_orders'] == 2
    assert perf_late['performance']['measured_orders'] == 2
    # 1 out of 2 on time -> 50%
    assert perf_late['performance']['otif_percentage'] == 50.0


@pytest.mark.asyncio
async def test_supplier_user_with_deleted_or_nonexistent_supplier(client, db_session):
    from tests.presentation.test_procurement_api import ok
    from src.infrastructure.auth.identity import UserModel
    import uuid

    # User bound to a non-existent supplier_id
    fake_sup_id = str(uuid.uuid4())
    user = ok(client.post('/users', json={
        'email': 'ghost-supplier@example.com',
        'role': 'USER',
        'password': 'test-password'
    }), 201)
    db_user = await db_session.get(UserModel, user['id'])
    db_user.role = 'SUPPLIER_USER'
    db_user.supplier_id = fake_sup_id
    await db_session.commit()

    token = ok(client.post('/auth/login', json={
        'tenantId': 'tenant-1',
        'email': user['email'],
        'password': 'test-password'
    }))['token']
    headers = {'Authorization': f'Bearer {token}'}

    res = client.get('/api/supplier-portal/pos', headers=headers)
    assert res.status_code == 404
    assert 'Supplier not found' in res.json()['detail']
