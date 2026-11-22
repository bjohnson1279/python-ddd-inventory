import pytest
from sqlalchemy import update
from src.infrastructure.conformance_models import ComplianceLedgerEntryModel


def _ensure_sku(client, sku):
    client.post('/products', json={'sku': sku, 'name': f'Product {sku}', 'price_amount': '10.00'})


def _receive(client, sku, qty, location='default', cost=100, lot=None, exp=None):
    _ensure_sku(client, sku)
    payload = {
        'sku': sku,
        'quantity': qty,
        'locationId': location,
        'unitCostCents': cost,
        'mutationId': f'mut-rcv-{sku}-{qty}-{lot or "none"}',
    }
    if lot:
        payload['lotNumber'] = lot
    if exp:
        payload['expirationDate'] = exp
    res = client.post('/inventory/receive', json=payload)
    assert res.status_code in (200, 201), res.text
    return res.json()


def _dispatch(client, sku, qty, location='default', lot=None):
    payload = {
        'sku': sku,
        'quantity': qty,
        'locationId': location,
        'mutationId': f'mut-dsp-{sku}-{qty}-{lot or "none"}',
    }
    if lot:
        payload['lotNumber'] = lot
    res = client.post('/inventory/dispatch', json=payload)
    assert res.status_code in (200, 201), res.text
    return res.json()


def test_rfid_assign_update_and_list(client):
    r1 = client.post('/rfid/assign', json={'epc': 'EPC-001', 'sku': 'SKU-1', 'serialNumber': 'SN-1'})
    assert r1.status_code == 201
    body1 = r1.json()
    assert body1['epc'] == 'EPC-001' and body1['sku'] == 'SKU-1' and body1['status'] == 'ACTIVE'

    # Re-assigning same EPC updates SKU/serial in place
    r2 = client.post('/rfid/assign', json={'epc': 'EPC-001', 'sku': 'SKU-2', 'serialNumber': 'SN-2'})
    assert r2.status_code == 201
    assert r2.json()['id'] == body1['id'] and r2.json()['sku'] == 'SKU-2'

    listed = client.get('/rfid/tags')
    assert listed.status_code == 200
    tags = listed.json()['tags']
    assert len(tags) == 1 and tags[0]['epc'] == 'EPC-001' and tags[0]['serialNumber'] == 'SN-2'


def test_cache_stats_hits_invalidation_and_clear(client):
    assert client.post('/admin/cache/clear').status_code == 200
    _receive(client, 'CACHE-SKU', 10)
    # First read -> miss, second read -> hit
    assert client.get('/inventory').status_code == 200
    assert client.get('/inventory').status_code == 200
    stats = client.get('/admin/cache/stats').json()
    assert stats['hits'] >= 1 and stats['misses'] >= 1 and stats['activeKeysCount'] >= 1

    # Mutation invalidates cache
    _dispatch(client, 'CACHE-SKU', 2)
    stats_after = client.get('/admin/cache/stats').json()
    assert stats_after['invalidations'] >= stats['invalidations']

    cleared = client.post('/admin/cache/clear').json()
    assert cleared['cleared'] is True
    assert client.get('/admin/cache/stats').json()['activeKeysCount'] == 0


def test_ai_anomaly_and_rebalance_endpoints(client):
    anom = client.get('/anomaly-detection/analyze')
    assert anom.status_code == 200
    assert anom.json()['alerts'] == [] and anom.json()['overallRiskScore'] == 0

    reb = client.get('/rebalance/matrix')
    assert reb.status_code == 200
    assert reb.json()['recommendations'] == [] and reb.json()['summary']['totalRecommendations'] == 0


@pytest.mark.asyncio
async def test_compliance_ledger_verify_tamper_reconstruct_and_replay(client, db_session):
    _receive(client, 'COMP-SKU', 10)
    entries_after_rcv = client.get('/compliance/ledger').json()
    assert len(entries_after_rcv) >= 1
    cutoff_ts = entries_after_rcv[-1]['timestamp']

    _dispatch(client, 'COMP-SKU', 3)
    entries = client.get('/compliance/ledger').json()
    assert len(entries) >= 2
    assert client.post('/compliance/verify').json()['isValid'] is True

    # Point-in-time reconstruction before dispatch should be 10; current should be 7
    hist = client.get('/compliance/reconstruct', params={'timestamp': cutoff_ts}).json()
    hist_bal = next(x['quantity'] for x in hist['stockLevels'] if x['sku'] == 'COMP-SKU')
    assert hist_bal == 10

    now_state = client.get('/compliance/reconstruct').json()
    now_bal = next(x['quantity'] for x in now_state['stockLevels'] if x['sku'] == 'COMP-SKU')
    assert now_bal == 7

    # Replay full and limited by timestamp
    full_replay = client.get('/compliance/replay').json()
    limited_replay = client.get('/compliance/replay', params={'timestamp': cutoff_ts}).json()
    assert len(limited_replay) < len(full_replay)

    # Tamper payload and verify chain failure detection
    target_id = entries[0]['id']
    await db_session.execute(
        update(ComplianceLedgerEntryModel)
        .where(ComplianceLedgerEntryModel.id == target_id)
        .values(payload='{"tampered": true}')
    )
    await db_session.commit()
    tampered = client.post('/compliance/verify').json()
    assert tampered['isValid'] is False
    assert tampered['failedSequenceNumber'] == entries[0]['sequenceNumber']

    # Tamper previous_hash link
    await db_session.execute(
        update(ComplianceLedgerEntryModel)
        .where(ComplianceLedgerEntryModel.id == target_id)
        .values(previous_hash='f' * 64)
    )
    await db_session.commit()
    broken_link = client.post('/compliance/verify').json()
    assert broken_link['isValid'] is False
    assert 'previousHash' in broken_link['reason']


def test_cross_dock_and_drop_ship(client):
    eval_res = client.post('/cross-dock/evaluate', json={
        'purchaseOrderId': 'PO-1',
        'inboundItems': [{'variantId': 'VAR-1', 'quantity': 7}],
        'backorders': [
            {'orderId': 'ORD-LOW', 'variantId': 'VAR-1', 'quantity': 5, 'priority': 1},
            {'orderId': 'ORD-HIGH', 'variantId': 'VAR-1', 'quantity': 5, 'priority': 10},
        ],
    })
    assert eval_res.status_code == 200
    rows = eval_res.json()
    assert len(rows) == 1
    assert rows[0]['recommendedCrossDockQuantity'] == 7
    assert rows[0]['matchingBackorders'][0] == {'orderId': 'ORD-HIGH', 'requiredQuantity': 5, 'priority': 10}
    assert rows[0]['matchingBackorders'][1] == {'orderId': 'ORD-LOW', 'requiredQuantity': 2, 'priority': 1}

    ds = client.post('/fulfillment/drop-ship', json={
        'orderId': 'ORD-DS-1',
        'variantId': 'VAR-DS',
        'quantity': 4,
        'supplierId': 'SUP-DS-1',
    })
    assert ds.status_code == 201
    assert ds.json()['status'] == 'DISPATCHED_TO_SUPPLIER'
    assert ds.json()['dropShipPoId'].startswith('PO-DS-')


def test_lot_fefo_quarantine_release_and_recall(client):
    _receive(client, 'LOT-SKU', 5, lot='LOT-LATE', exp='2028-12-01T00:00:00Z')
    _receive(client, 'LOT-SKU', 4, lot='LOT-EARLY', exp='2027-01-01T00:00:00Z')

    picks = client.get('/inventory/fefo-pick', params={'sku': 'LOT-SKU', 'quantity': 6}).json()
    assert picks[0]['lotNumber'] == 'LOT-EARLY' and picks[0]['quantity'] == 4
    assert picks[1]['lotNumber'] == 'LOT-LATE' and picks[1]['quantity'] == 2

    # Quarantine LOT-EARLY so FEFO skips it
    q = client.post('/lots/quarantine', json={'lotNumber': 'LOT-EARLY', 'reason': 'Inspection'})
    assert q.status_code == 200 and q.json()['status'] == 'QUARANTINED'
    picks_after_q = client.get('/inventory/fefo-pick', params={'sku': 'LOT-SKU', 'quantity': 4}).json()
    assert all(p['lotNumber'] != 'LOT-EARLY' for p in picks_after_q)

    # Release LOT-EARLY and dispatch from it, then trace recall
    rel = client.post('/lots/release', json={'lotNumber': 'LOT-EARLY'})
    assert rel.status_code == 200 and rel.json()['status'] == 'AVAILABLE'
    _dispatch(client, 'LOT-SKU', 3, lot='LOT-EARLY')

    recall = client.get('/inventory/reports/recall/LOT-EARLY').json()
    assert len(recall) >= 1 and sum(r['quantity'] for r in recall) == 3


def test_forecasting_report(client):
    _receive(client, 'FC-SKU', 20)
    _dispatch(client, 'FC-SKU', 7)
    rep = client.get('/forecasting/report', params={'locationId': 'default'}).json()
    row = next(r for r in rep if r['sku'] == 'FC-SKU')
    assert row['currentStock'] == 13
    assert row['salesVelocity7d'] == 1.0
    assert row['salesVelocity30d'] > 0 and row['forecastedDemand30d'] > 0


def test_warehouse_locations_putaway_pick_route_and_slotting(client):
    # Empty warehouse returns empty putaway & slotting
    assert client.post('/warehouse-locations/putaway-suggestions', json={'sku': 'WH-SKU', 'quantity': 5}).json() == []
    assert client.get('/warehouse-locations/slotting-suggestions').json() == []

    near = client.post('/warehouse-locations', json={
        'warehouseId': 'WH1', 'zone': 'A', 'aisle': '01', 'rack': '01', 'shelf': '01', 'bin': '01'
    }).json()
    far = client.post('/warehouse-locations', json={
        'warehouseId': 'WH1', 'zone': 'A', 'aisle': '99', 'rack': '01', 'shelf': '01', 'bin': '01'
    }).json()
    # Upsert existing bin updates capacity
    updated = client.post('/warehouse-locations', json={
        'id': near['id'], 'warehouseId': 'WH1', 'zone': 'A', 'aisle': '01', 'rack': '01', 'shelf': '01', 'bin': '01',
        'maxWeightGrams': 50000
    }).json()
    assert updated['maxWeightGrams'] == 50000

    listed = client.get('/warehouse-locations').json()
    assert len(listed) == 2

    putaway = client.post('/warehouse-locations/putaway-suggestions', json={'sku': 'WH-SKU', 'quantity': 5}).json()
    assert len(putaway) == 1 and putaway[0]['locationId'] == near['id']

    routes = client.post('/warehouse-locations/optimize-pick-route', json={
        'items': [
            {'sku': 'S1', 'locationId': far['id'], 'quantity': 1},
            {'sku': 'S2', 'locationId': near['id'], 'quantity': 2},
        ]
    }).json()
    assert len(routes) == 1
    assert [it['locationId'] for it in routes[0]['items']] == [near['id'], far['id']]

    _receive(client, 'SLOT-SKU', 20, location=far['id'])
    _dispatch(client, 'SLOT-SKU', 10, location=far['id'])
    slotting = client.get('/warehouse-locations/slotting-suggestions').json()
    assert any(
        s['sku'] == 'SLOT-SKU' and s['currentLocationId'] == far['id']
        and s['recommendedLocationId'] == near['id'] and s['estimatedSavings'] > 0
        for s in slotting
    )

    assert client.delete(f"/warehouse-locations/{far['id']}").json()['deleted'] is True


def test_generic_webhook_subscriptions_and_deliveries(client):
    created = client.post('/webhooks/subscriptions', json={
        'targetUrl': 'https://192.0.2.1/hook',
        'secret': 'wh-sec',
        'eventTypes': ['StockReceived'],
    })
    assert created.status_code == 201
    sub = created.json()
    assert sub['isActive'] is True

    subs = client.get('/webhooks/subscriptions').json()
    assert any(s['id'] == sub['id'] for s in subs)

    _receive(client, 'WH-DEL-SKU', 5)
    deliveries = client.get('/webhooks/deliveries').json()
    assert any(d['subscriptionId'] == sub['id'] and d['eventType'] == 'StockReceived' for d in deliveries)

    upd = client.put(f"/webhooks/subscriptions/{sub['id']}", json={'isActive': False})
    assert upd.status_code == 200 and upd.json()['isActive'] is False
    assert client.put('/webhooks/subscriptions/missing-id', json={'isActive': False}).status_code == 404

    assert client.delete(f"/webhooks/subscriptions/{sub['id']}").json()['deleted'] is True
