import asyncio
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.infrastructure.test_outbox_contract import outbox_database


@pytest.mark.asyncio
async def test_serial_and_kit_concurrent_commands_and_forced_parent_ownership(outbox_database):
    client, sessions, runtime, backend = outbox_database
    async def enroll(tenant):
        setup = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
            'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
        assert setup.status_code == 201, setup.text
        token = (await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        for sku in ('SERIAL', 'PART', 'KIT'):
            assert (await client.post('/products', headers=headers, json={'sku': sku, 'name': sku, 'price_amount': '1.00'})).status_code == 201
        return headers
    a, b = await enroll('store-a'), await enroll('store-b')
    command = {'sku': 'SERIAL', 'serialNumber': 'SN-1', 'locationId': 'shop', 'mutationId': 'register', 'reason': 'Register'}
    registrations = await asyncio.gather(*(client.post('/serials', headers=a, json=command) for _ in range(2)))
    assert [r.status_code for r in registrations] == [201, 201], [r.text for r in registrations]
    assert registrations[0].json() == registrations[1].json()
    assert (await client.get('/serials/trace/SN-1', headers=b)).status_code == 404
    assert (await client.post('/serials', headers=b, json=command)).status_code == 201
    receipt = {'expectedVersion': 0, 'mutationId': 'receive', 'reason': 'Receipt', 'referenceId': 'receipt', 'unitCostCents': 101}
    received = await asyncio.gather(*(client.post('/serials/SN-1/receive', headers=a, json=receipt) for _ in range(2)))
    assert [r.status_code for r in received] == [200, 200], [r.text for r in received]
    sales = await asyncio.gather(*(client.post('/serials/SN-1/sell', headers=a, json={
        'expectedVersion': 1, 'mutationId': 'sale-' + str(i), 'reason': 'Sale', 'referenceId': 'sale-' + str(i)}) for i in range(2)))
    assert sorted(r.status_code for r in sales) == [200, 409], [r.text for r in sales]
    assert (await client.get('/inventory', headers=a)).json()[0]['quantity'] == 0
    assert (await client.post('/inventory/receive', headers=a, json={'sku': 'PART', 'locationId': 'shop', 'quantity': 2, 'unitCostCents': 51})).status_code == 200
    formula = {'sku': 'KIT', 'name': 'Bundle', 'components': [{'sku': 'PART', 'quantity': 2}], 'expectedVersion': 0, 'mutationId': 'formula', 'reason': 'Formula'}
    defined = await client.post('/kits', headers=a, json=formula)
    assert defined.status_code == 201, defined.text
    assert (await client.get('/kits/KIT', headers=b)).status_code == 404
    builds = await asyncio.gather(*(client.post('/kits/assemble', headers=a, json={'kitSku': 'KIT', 'locationId': 'shop',
        'quantity': 1, 'expectedVersion': 1, 'stockVersions': {'PART': 1, 'KIT': 0}, 'mutationId': 'build-' + str(i),
        'reason': 'Assembly', 'referenceId': 'build-' + str(i)}) for i in range(2)))
    assert sorted(r.status_code for r in builds) == [200, 409], [r.text for r in builds]
    second_serial = {**command, 'serialNumber': 'SN-2', 'mutationId': 'register-second'}
    assert (await client.post('/serials', headers=a, json=second_serial)).status_code == 201
    assert (await client.post('/serials/SN-2/receive', headers=a, json={**receipt, 'mutationId': 'receive-second'})).status_code == 200
    transfer = await client.post('/serials/SN-2/transfer', headers=a, json={'expectedVersion': 1, 'mutationId': 'serial-transit',
        'reason': 'Relocate', 'referenceId': 'shipment', 'destinationId': 'branch'})
    assert transfer.status_code == 200, transfer.text
    assert (await client.post('/inventory/receive', headers=a, json={'sku': 'PART', 'locationId': 'shop', 'quantity': 2, 'unitCostCents': 51})).status_code == 200
    bundle = await client.post('/kits/sale', headers=a, json={'kitSku': 'KIT', 'locationId': 'shop', 'quantity': 1,
        'expectedVersion': 1, 'stockVersions': {'PART': 3}, 'mutationId': 'bundle-sale', 'reason': 'Sale', 'referenceId': 'bundle-sale'})
    assert bundle.status_code == 200 and bundle.json()['costOfGoodsSoldCents'] == 102, bundle.text
    vendor = (await client.post('/suppliers', headers=a, json={'name': 'Vendor', 'mutationId': 'vendor'})).json()
    for sku, location in [('PART', 'shop'), ('SERIAL', 'branch')]:
        policy = await client.post('/reorder-policies', headers=a, json={'sku': sku, 'locationId': location, 'supplierId': vendor['id'],
            'reorderPoint': 2, 'mutationId': 'policy-' + sku})
        assert policy.status_code == 200, policy.text
    suggestions = {(s['sku'], s['locationId']): s for s in (await client.get('/reorder-policies/suggestions', headers=a)).json()}
    assert suggestions[('PART', 'shop')]['soldLast30Days'] == 2
    assert suggestions[('SERIAL', 'branch')]['incomingQuantity'] == 1
    if backend == 'postgres':
        async with runtime.begin() as conn:
            assert (await conn.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user'))).one() == (False, False)
            for table in ('python_serial_tracking', 'python_serials', 'python_kits', 'python_kit_builds'):
                assert await conn.scalar(text('SELECT count(*) FROM ' + table)) == 0
                assert await conn.scalar(text("SELECT relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)"), {'name': table})
            await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
            assert await conn.scalar(text('SELECT count(*) FROM python_serials')) == 2
            assert await conn.scalar(text('SELECT count(*) FROM python_kit_builds')) == 1
            foreign_product = await conn.scalar(text("SELECT variant_id FROM python_serials LIMIT 1"))
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-b', true)"))
                await conn.execute(text("INSERT INTO python_serials (id,tenant_id,sku,variant_id,serial_number,status,location_id,version,history) "
                    "VALUES ('wrong-parent','store-b','SERIAL',:foreign,'FOREIGN','pending','shop',0,'[]')"), {'foreign': foreign_product})
        assert denied.value.orig.sqlstate == '42501'
        assert (await client.post('/products', headers=b, json={'sku': 'ONLY-B', 'name': 'Private', 'price_amount': '1.00'})).status_code == 201
        assert (await client.post('/inventory/receive', headers=a, json={'sku': 'KIT', 'locationId': 'shop', 'quantity': 1, 'unitCostCents': 1})).status_code == 200
        async with runtime.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
            kit_id = await conn.scalar(text("SELECT id FROM python_kits WHERE sku='KIT'"))
            layer_id = await conn.scalar(text("SELECT id FROM python_cost_layers l WHERE sku='KIT' AND NOT EXISTS (SELECT 1 FROM python_kit_builds b WHERE b.layer_id=l.id) LIMIT 1"))
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                await conn.execute(text("INSERT INTO python_kit_builds (id,tenant_id,kit_id,layer_id,components,disassembled) VALUES ('foreign-component','store-a',:kit,:layer,:components,false)"),
                    {'kit': kit_id, 'layer': layer_id, 'components': '[{"sku":"ONLY-B","quantity":1,"costCents":1}]'})
        assert denied.value.orig.sqlstate == '42501'
