import pytest
import jwt
from sqlalchemy import select
from src.infrastructure.auth.identity import UserModel, SecurityAuditModel, jwt_secret
from src.infrastructure.cycle_count.models import CycleCountRecordModel, CycleCountLineItemModel
from src.infrastructure.owned_feature_models import NotificationModel


def staff(client, role='USER', email='staff@example.com', **fields):
    created = client.post('/users', json={'email': email, 'role': role, 'password': 'test-password', **fields})
    assert created.status_code == 201, created.text
    response = client.post('/auth/login', headers={'Authorization': ''}, json={
        'tenantId': 'tenant-1', 'email': email, 'password': 'test-password'})
    assert response.status_code == 200, response.text
    return created.json(), {'Authorization': 'Bearer ' + response.json()['token']}


def other_store(client):
    created = client.post('/auth/setup', headers={'Authorization': ''}, json={
        'tenantId': 'other', 'orgName': 'Other', 'adminName': 'Other Admin',
        'adminEmail': 'other@example.com', 'adminPassword': 'test-password'})
    assert created.status_code == 201
    token = client.post('/auth/login', headers={'Authorization': ''}, json={
        'tenantId': 'other', 'email': 'other@example.com', 'password': 'test-password'}).json()['token']
    return created.json()['userId'], {'Authorization': 'Bearer ' + token}


@pytest.mark.asyncio
async def test_logout_revokes_only_current_session(client):
    second = client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'admin@example.com',
                                            'password': 'test-password'}).json()['token']
    assert client.post('/auth/logout').status_code == 200
    assert client.get('/products').status_code == 401
    assert client.get('/products', headers={'Authorization': 'Bearer ' + second}).status_code == 200


@pytest.mark.asyncio
async def test_role_changes_and_disable_revoke_existing_tokens(client, db_session):
    user, headers = staff(client)
    assert client.post('/inventory/receive', headers=headers, json={'sku': 'SKU-A', 'quantity': 1}).status_code == 403
    assert client.patch('/users/' + user['id'] + '/role', json={'role': 'MANAGER'}).status_code == 200
    assert client.get('/products', headers=headers).status_code == 401
    token = client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'],
                                           'password': 'test-password'}).json()['token']
    manager = {'Authorization': 'Bearer ' + token}
    assert client.post('/inventory/products', headers=manager, json={'id': 'managed', 'name': 'Managed'}).status_code == 201
    assert client.post('/users', headers=manager, json={'email': 'hijack@example.com', 'role': 'ADMIN'}).status_code == 403
    assert client.patch('/users/' + user['id'] + '/status', json={'isActive': False}).status_code == 200
    assert client.get('/products', headers=manager).status_code == 401
    assert client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'],
                                          'password': 'test-password'}).status_code == 401
    actions = list(await db_session.scalars(select(SecurityAuditModel.action)))
    assert 'role-changed' in actions and 'user-disabled' in actions


@pytest.mark.asyncio
async def test_last_admin_and_cross_tenant_user_changes_are_protected(client):
    admin = client.get('/auth/me').json()
    assert 'password_hash' not in admin and 'passwordHash' not in admin
    assert client.patch('/users/' + admin['id'] + '/role', json={'role': 'USER'}).status_code == 409
    assert client.patch('/users/' + admin['id'] + '/status', json={'isActive': False}).status_code == 409
    other_id, other = other_store(client)
    assert client.patch('/users/' + other_id + '/role', json={'role': 'ADMIN'}).status_code == 404
    assert client.post('/users/' + other_id + '/revoke-sessions').status_code == 404
    assert client.get('/users', headers=other).json()['users'][0]['id'] == other_id
    assert client.get('/auth/me').json()['role'] == 'ADMIN'


@pytest.mark.asyncio
async def test_password_change_revokes_all_sessions(client):
    assert client.post('/auth/password', json={'currentPassword': 'wrong', 'newPassword': 'new-test-password'}).status_code == 401
    assert client.post('/auth/password', json={'currentPassword': 'test-password', 'newPassword': 'new-test-password'}).status_code == 200
    assert client.get('/products').status_code == 401
    assert client.post('/auth/login', headers={'Authorization': ''}, json={
        'tenantId': 'tenant-1', 'email': 'admin@example.com', 'password': 'test-password'}).status_code == 401
    assert client.post('/auth/login', headers={'Authorization': ''}, json={
        'tenantId': 'tenant-1', 'email': 'admin@example.com', 'password': 'new-test-password'}).status_code == 200


@pytest.mark.asyncio
async def test_login_throttle_persists_and_ignores_forwarded_ip_spoofing(client, monkeypatch):
    import time
    from types import SimpleNamespace
    from src.infrastructure.auth import security
    clock = [int(time.time())]
    monkeypatch.setattr(security, 'time', SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr('src.presentation.auth_router.verify_password', lambda *args: False)
    # A new account avoids relying on the fixture login's rate-limit bucket.
    payload = {'tenantId': 'tenant-1', 'email': 'rate-limit@example.com', 'password': 'wrong'}
    for _ in range(10):
        assert client.post('/auth/login', json=payload).status_code == 401
    denied = client.post('/auth/login', json=payload)
    assert denied.status_code == 429 and int(denied.headers['Retry-After']) > 0
    assert client.post('/api/auth/login', headers={'X-Forwarded-For': 'new-address'}, json=payload).status_code == 429
    clock[0] += 300
    assert client.post('/auth/login', json=payload).status_code == 401


@pytest.mark.asyncio
async def test_registration_can_be_disabled_and_explicitly_bootstrapped(client, monkeypatch):
    monkeypatch.delenv('ALLOW_PUBLIC_SIGNUP')  # Closed by default, not just when explicitly disabled.
    monkeypatch.setenv('SETUP_TOKEN', 'test-setup-only')
    payload = {'tenantId': 'new', 'orgName': 'New', 'adminName': 'Admin',
               'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'}
    assert client.post('/auth/setup', json=payload).status_code == 403
    assert client.post('/auth/setup', headers={'X-Setup-Token': 'test-setup-only'}, json=payload).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['USER', 'VIEWER', 'ACCOUNTANT', 'WAREHOUSE_OPERATOR', 'MANAGER'])
async def test_server_permission_matrix(client, role):
    user, headers = staff(client, role)
    assert client.get('/products', headers=headers).status_code == 200
    assert client.get('/users', headers=headers).status_code == 403
    expected_write = 201 if role == 'MANAGER' else 403
    assert client.post('/inventory/products', headers=headers, json={'id': 'new', 'name': 'New'}).status_code == expected_write
    config_status = 200 if role in ('ACCOUNTANT', 'MANAGER') else 403
    assert client.post('/accounting/tenant-config', headers=headers, json={
        'tenantId': 'tenant-1', 'costingMethod': 'FIFO'}).status_code == config_status
    # Even a signed token cannot turn a persisted reader into an administrator.
    claims = jwt.decode(headers['Authorization'].split()[1], jwt_secret(), algorithms=['HS256'],
                        audience='inventory-client', issuer='python-ddd-inventory')
    claims['roles'] = ['ADMIN']
    forged = {'Authorization': 'Bearer ' + jwt.encode(claims, jwt_secret(), algorithm='HS256')}
    assert client.get('/users', headers=forged).status_code == 403


@pytest.mark.asyncio
async def test_all_tenant_selectors_and_nested_body_selectors_are_checked(client):
    assert client.get('/products?tenantId=tenant-1&tenantId=other').status_code == 403
    assert client.get('/products?tenant_id=other', headers={'X-Tenant-ID': 'tenant-1'}).status_code == 403
    assert client.post('/reports/', json={'name': 'Bad', 'filters': {'tenantId': 'other'}}).status_code == 403
    assert client.post('/api/cycle-counts/plans', json={'tenant_id': 'other', 'name': 'Bad',
        'abc_classification': 'A', 'frequency_days': 1}).status_code == 403


@pytest.mark.asyncio
async def test_count_tenant_and_assignment_scopes(client, db_session):
    operator, headers = staff(client, 'WAREHOUSE_OPERATOR')
    admin_id = client.get('/auth/me').json()['id']
    for record_id, tenant, actor in [('owned', 'tenant-1', operator['id']),
                                     ('unassigned', 'tenant-1', admin_id), ('foreign', 'other', operator['id'])]:
        db_session.add(CycleCountRecordModel(id=record_id, tenant_id=tenant, assigned_operator_id=actor,
                                            name='Count', status='PENDING', abc_classification='A'))
        db_session.add(CycleCountLineItemModel(record_id=record_id, sku='COUNT-SKU', expected_quantity=10))
    await db_session.commit()
    assigned = client.get('/api/cycle-counts/assigned', headers=headers).json()
    assert [record['record_id'] for record in assigned] == ['owned']
    assert assigned[0]['items'][0]['expected_quantity'] is None
    for record_id in ('unassigned', 'foreign', 'missing'):
        payload = {'record_id': record_id, 'revision': 0, 'mutation_id': 'sync-' + record_id,
                   'items': [{'sku': 'COUNT-SKU', 'counted_quantity': 10}]}
        assert client.post('/api/cycle-counts/submit', headers=headers, json=payload).status_code == 404
        assert client.post('/api/cycle-counts/sync', headers=headers, json=[payload]).status_code == 404
    assert client.post('/api/cycle-counts/submit', headers=headers, json={
        'record_id': 'owned', 'items': [{'sku': 'COUNT-SKU', 'counted_quantity': 10}]}).status_code == 200


@pytest.mark.asyncio
async def test_report_and_graphql_data_are_tenant_owned(client):
    report = client.post('/reports/', json={'name': 'Private'}).json()['report']
    product = client.post('/products', json={'sku': 'PRIVATE-SKU', 'name': 'Private', 'price_amount': '1.00'}).json()
    _, other = other_store(client)
    assert client.get('/reports/', headers=other).json()['reports'] == []
    assert client.post('/reports/' + report['id'] + '/execute', headers=other).status_code == 404
    assert client.post('/reports/' + report['id'] + '/schedule', headers=other).status_code == 404
    response = client.post('/graphql', headers=other, json={'query': '{ products { id sku } }'})
    assert response.status_code == 200 and response.json()['data']['products'] == []
    response = client.post('/graphql', headers=other, json={
        'query': '{ product(id: "' + product['id'] + '") { id } }'})
    assert response.json()['data']['product'] is None


@pytest.mark.asyncio
async def test_notifications_cannot_select_another_recipient_or_tenant_manager(client, db_session):
    employee, headers = staff(client)
    admin_id = client.get('/auth/me').json()['id']
    other_id, _ = other_store(client)
    db_session.add(NotificationModel(id='staff-message', tenant_id='tenant-1', user_id=employee['id'], message='Private'))
    db_session.add(NotificationModel(id='admin-message', tenant_id='tenant-1', user_id=admin_id, message='Private'))
    db_session.add(NotificationModel(id='foreign-message', tenant_id='other', user_id=other_id, message='Private'))
    await db_session.commit()
    assert client.get('/api/notifications/inbox/unread', headers=headers).json()['unread_count'] == 1
    assert client.get('/api/notifications/inbox/unread?user_id=' + admin_id, headers=headers).status_code == 403
    assert client.post('/api/notifications/admin-message/read', headers=headers).status_code == 404
    assert client.post('/api/notifications/foreign-message/read', headers=headers).status_code == 404
    assert client.post('/api/notifications/staff-message/escalate', headers=headers,
                       json={'target_manager_id': other_id}).status_code == 404
    assert client.post('/api/notifications/staff-message/escalate', headers=headers,
                       json={'target_manager_id': admin_id}).status_code == 200


@pytest.mark.asyncio
async def test_supplier_feature_requires_bound_role_and_real_persistence(client):
    assert client.get('/api/supplier-portal/pos').status_code == 403
    assert client.post('/users', json={'email': 'supplier@example.com', 'role': 'SUPPLIER_USER'}).status_code == 422
    supplier = client.post('/suppliers', json={'name': 'Supplier A', 'mutationId': 'supplier-a'}).json()
    _, headers = staff(client, 'SUPPLIER_USER', supplierId=supplier['id'])
    assert client.get('/api/supplier-portal/pos', headers=headers).json() == {'pos': []}
    assert client.get('/products', headers=headers).status_code == 403


@pytest.mark.asyncio
async def test_warehouse_can_move_stock_without_pricing_or_reading_cogs(client):
    _, operator = staff(client, 'WAREHOUSE_OPERATOR')
    client.post('/products', json={'sku': 'OPERATIONS-SKU', 'name': 'Item', 'price_amount': '99.00'})
    assert client.post('/inventory/receive', json={'sku': 'OPERATIONS-SKU', 'quantity': 3,
                                                 'unitCostCents': 25}).status_code == 200
    assert client.post('/inventory/receive', headers=operator, json={
        'sku': 'OPERATIONS-SKU', 'quantity': 1, 'unitCostCents': 0}).status_code == 403
    sold = client.post('/inventory/dispatch', headers=operator, json={'sku': 'OPERATIONS-SKU', 'quantity': 1})
    assert sold.status_code == 200 and sold.json()['quantity'] == 2
    assert 'costOfGoodsSoldCents' not in sold.json()
    movement = next(row for row in client.get('/inventory/movements').json() if row['operation'] == 'dispatch')
    assert movement['result']['costOfGoodsSoldCents'] == 25
    assert client.get('/accounting/cost-layers', headers=operator).status_code == 403


@pytest.mark.asyncio
async def test_role_definitions_match_shared_client_contract(client):
    response = client.get('/roles')
    assert response.headers['Cache-Control'] == 'no-store'
    roles = response.json()
    assert roles and all(role['isCustom'] is False and role['tenantId'] == 'tenant-1' for role in roles)
    assert all({'id', 'resource', 'action'} <= permission.keys() for role in roles for permission in role['permissions'])
    assert client.get('/roles/permissions').json()[0]['resource']
