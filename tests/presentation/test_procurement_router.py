import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from src.presentation.procurement_router import (
    execute,
    service,
    CommandDTO,
)
from src.application.procurement_service import ProcurementNotFound
from src.application.stock_service import StockConflict


@pytest.mark.asyncio
async def test_execute_success_and_exception_mappings():
    mock_req = MagicMock()
    mock_req.state.user_id = 'user-1'
    mock_session = AsyncMock()

    mock_svc = MagicMock()
    mock_svc.execute = AsyncMock(return_value={'status': 'ok'})

    with patch('src.presentation.procurement_router.service', return_value=mock_svc):
        # Dict payload
        res = await execute(mock_req, mock_session, 'test_op', {'mutationId': 'm-1', 'foo': 'bar'})
        assert res == {'status': 'ok'}
        mock_svc.execute.assert_awaited_with('test_op', {'foo': 'bar'}, 'm-1')

        # BaseModel payload
        cmd = CommandDTO(mutationId='m-2', expectedVersion=1, reason='Valid reason')
        res2 = await execute(mock_req, mock_session, 'test_op', cmd)
        assert res2 == {'status': 'ok'}
        mock_svc.execute.assert_awaited_with('test_op', {'expectedVersion': 1, 'reason': 'Valid reason'}, 'm-2')

        # ProcurementNotFound -> HTTPException(404)
        mock_svc.execute.side_effect = ProcurementNotFound('Not found item')
        with pytest.raises(HTTPException) as exc_info_404:
            await execute(mock_req, mock_session, 'test_op', {'mutationId': 'm-3'})
        assert exc_info_404.value.status_code == 404
        assert exc_info_404.value.detail == 'Not found item'

        # StockConflict -> HTTPException(409)
        mock_svc.execute.side_effect = StockConflict('Conflict occurred')
        with pytest.raises(HTTPException) as exc_info_409:
            await execute(mock_req, mock_session, 'test_op', {'mutationId': 'm-4'})
        assert exc_info_409.value.status_code == 409
        assert exc_info_409.value.detail == 'Conflict occurred'

        # ValueError -> HTTPException(400)
        mock_svc.execute.side_effect = ValueError('Invalid data')
        with pytest.raises(HTTPException) as exc_info_400:
            await execute(mock_req, mock_session, 'test_op', {'mutationId': 'm-5'})
        assert exc_info_400.value.status_code == 400
        assert exc_info_400.value.detail == 'Invalid data'


@pytest.mark.asyncio
async def test_service_helper_initialization():
    mock_req = MagicMock()
    mock_req.state.user_id = 'test-user-id'
    mock_session = AsyncMock()

    with patch('src.presentation.procurement_router.current_tenant', return_value='tenant-xyz'):
        svc = service(mock_req, mock_session)
        assert svc.tenant == 'tenant-xyz'
        assert svc.actor == 'test-user-id'
        assert svc.session == mock_session


@pytest.mark.asyncio
async def test_update_supplier_explicit_null_rejection(client):
    supplier = client.post('/suppliers', json={'name': 'Valid Supplier', 'leadTimeDays': 5, 'mutationId': 's-init-1'}).json()
    supplier_id = supplier['id']

    # Explicit null name
    resp = client.patch(f'/suppliers/{supplier_id}', json={'name': None, 'expectedVersion': 0, 'mutationId': 'm-null-1'})
    assert resp.status_code == 422
    assert 'cannot be null' in resp.json()['detail']

    # Explicit null leadTimeDays
    resp2 = client.patch(f'/suppliers/{supplier_id}', json={'leadTimeDays': None, 'expectedVersion': 0, 'mutationId': 'm-null-2'})
    assert resp2.status_code == 422
    assert 'cannot be null' in resp2.json()['detail']

    # Explicit null isActive
    resp3 = client.patch(f'/suppliers/{supplier_id}', json={'isActive': None, 'expectedVersion': 0, 'mutationId': 'm-null-3'})
    assert resp3.status_code == 422
    assert 'cannot be null' in resp3.json()['detail']


@pytest.mark.asyncio
async def test_purchase_order_get_not_found(client):
    with patch('src.presentation.procurement_router.ProcurementService.po', side_effect=ProcurementNotFound('Purchase order not found')):
        resp = client.get('/purchase-orders/mocked-missing-po')
        assert resp.status_code == 404
        assert resp.json()['detail'] == 'Purchase order not found'


@pytest.mark.asyncio
async def test_order_asns_endpoints(client):
    # Nonexistent PO -> 404 via ProcurementNotFound in po()
    with patch('src.presentation.procurement_router.ProcurementService.po', side_effect=ProcurementNotFound('PO not found for ASN lookup')):
        resp = client.get('/purchase-orders/missing-po-for-asn/asns')
        assert resp.status_code == 404
        assert resp.json()['detail'] == 'PO not found for ASN lookup'

    # Existing PO with no ASNs -> empty list
    sup = client.post('/suppliers', json={'name': 'ASN Supplier', 'mutationId': 'asn-sup-1'}).json()
    client.post('/products', json={'sku': 'SKU-ASN', 'name': 'SKU ASN', 'price_amount': '12.00'})
    order = client.post('/purchase-orders', json={
        'supplierId': sup['id'],
        'items': [{'sku': 'SKU-ASN', 'quantity': 10, 'unitCostCents': 200}],
        'mutationId': 'po-asn-1'
    }).json()

    resp_asns = client.get(f"/purchase-orders/{order['id']}/asns")
    assert resp_asns.status_code == 200
    assert resp_asns.json() == []

    # Create an ASN for the purchase order and verify it is returned
    client.post('/purchase-orders/' + order['id'] + '/approve', json={'mutationId': 'appr-1'})
    client.post('/purchase-orders/' + order['id'] + '/send', json={'mutationId': 'send-1'})
    user = client.post('/users', json={'email': 'asn-supplier@example.com', 'role': 'SUPPLIER_USER',
        'supplierId': sup['id'], 'password': 'test-password'}).json()
    token = client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'test-password'}).json()['token']
    asn_headers = {'Authorization': 'Bearer ' + token}
    asn_data = {'po_id': order['id'], 'tracking_number': 'TRACK-ASN-1', 'estimated_delivery_date': '2030-01-01T00:00:00Z',
                'items': [{'sku': 'SKU-ASN', 'shipped_quantity': 10}], 'mutationId': 'asn-mut-1'}
    asn_res = client.post('/api/supplier-portal/asns', headers=asn_headers, json=asn_data).json()
    resp_asns_after = client.get(f"/purchase-orders/{order['id']}/asns")
    assert resp_asns_after.status_code == 200
    assert len(resp_asns_after.json()) == 1
    assert resp_asns_after.json()[0]['id'] == asn_res['asn_id']


@pytest.mark.asyncio
async def test_cancel_requires_reason(client):
    sup = client.post('/suppliers', json={'name': 'Cancel Supplier', 'mutationId': 'cancel-sup-1'}).json()
    client.post('/products', json={'sku': 'SKU-CANCEL', 'name': 'SKU Cancel', 'price_amount': '5.00'})
    order = client.post('/purchase-orders', json={
        'supplierId': sup['id'],
        'items': [{'sku': 'SKU-CANCEL', 'quantity': 5, 'unitCostCents': 100}],
        'mutationId': 'po-cancel-1'
    }).json()

    # Cancel without reason -> 400
    resp = client.post(f"/purchase-orders/{order['id']}/cancel", json={'mutationId': 'mut-cancel-fail'})
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'Cancellation requires a reason'


@pytest.mark.asyncio
async def test_reorder_policies_pagination_and_suggestions_error(client):
    sup = client.post('/suppliers', json={'name': 'Policy Supplier', 'mutationId': 'pol-sup-1'}).json()
    client.post('/products', json={'sku': 'SKU-P1', 'name': 'SKU P1', 'price_amount': '1.00'})
    client.post('/reorder-policies', json={
        'sku': 'SKU-P1', 'supplierId': sup['id'], 'reorderPoint': 10, 'mutationId': 'pol-create-1'
    })

    # Test limit and offset on policies
    resp = client.get('/reorder-policies?limit=1&offset=0')
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Suggestions evaluate endpoint handles ValueError -> 400
    with patch('src.presentation.procurement_router.ProcurementService.suggestions', side_effect=ValueError('Evaluation error')):
        resp_eval = client.post('/reorder-policies/evaluate')
        assert resp_eval.status_code == 400
        assert resp_eval.json()['detail'] == 'Evaluation error'

        resp_sugg = client.get('/reorder-policies/suggestions')
        assert resp_sugg.status_code == 400
        assert resp_sugg.json()['detail'] == 'Evaluation error'


@pytest.mark.asyncio
async def test_reorder_policy_draft_missing_expected_version(client):
    sup = client.post('/suppliers', json={'name': 'Draft Policy Supplier', 'mutationId': 'pol-sup-2'}).json()
    client.post('/products', json={'sku': 'SKU-P2', 'name': 'SKU P2', 'price_amount': '2.00'})
    policy = client.post('/reorder-policies', json={
        'sku': 'SKU-P2', 'supplierId': sup['id'], 'reorderPoint': 10, 'mutationId': 'pol-create-2'
    }).json()

    # Post draft without expectedVersion -> 400
    resp = client.post(f"/reorder-policies/{policy['id']}/draft", json={'mutationId': 'draft-fail'})
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'Policy version is required'
