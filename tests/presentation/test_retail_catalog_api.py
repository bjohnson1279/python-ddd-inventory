import pytest


@pytest.mark.asyncio
async def test_catalog_variant_barcode_receiving_and_sale(client):
    assert client.post('/inventory/products', json={'id': 'shirt', 'name': 'T Shirt'}).status_code == 201
    assert client.get('/inventory/products').json()[0]['variants'] == []
    variant = {'sku': 'RED-SHIRT', 'trackingMode': 'quantity',
               'attributes': [{'name': 'Color', 'value': 'Red'}]}
    assert client.post('/inventory/products/shirt/variants', json=variant).status_code == 201
    assert client.post('/inventory/products/shirt/variants', json=variant).status_code == 409
    assert client.post('/barcodes/assign', json={'sku': 'RED-SHIRT', 'barcodeValue': '123456',
                                               'makePrimary': True}).status_code == 201
    catalog = client.get('/inventory/products').json()
    assert catalog[0]['name'] == 'T Shirt'
    assert catalog[0]['variants'][0]['attributes'] == variant['attributes']
    assert catalog[0]['variants'][0]['barcodes'][0]['barcode']['value'] == '123456'
    scan = {'barcodeValue': '123456', 'context': 'receiving', 'scannedAmount': 5,
            'locationId': 'store', 'mutationId': 'receipt-scan'}
    assert client.post('/barcodes/scan', json=scan).json()['quantity'] == 5
    assert client.post('/barcodes/scan', json=scan).json()['quantity'] == 5
    sale = {**scan, 'context': 'pos', 'scannedAmount': 2, 'mutationId': 'sale-scan'}
    assert client.post('/barcodes/scan', json=sale).json()['quantity'] == 3
    assert client.get('/inventory').json()[0]['sku'] == 'RED-SHIRT'


@pytest.mark.asyncio
async def test_catalog_failures_do_not_create_variants_or_change_primary_barcode(client):
    client.post('/inventory/products', json={'id': 'item', 'name': 'Item'})
    assert client.post('/inventory/products/missing/variants', json={'sku': 'SKU-A'}).status_code == 404
    assert client.post('/inventory/products/item/variants', json={'sku': 'invalid sku'}).status_code == 400
    assert client.post('/inventory/products/item/variants', json={'sku': 'SKU-A', 'trackingMode': 'serial'}).status_code == 422
    client.post('/inventory/products/item/variants', json={'sku': 'SKU-A'})
    barcode = {'sku': 'SKU-A', 'barcodeValue': 'primary', 'makePrimary': True}
    assert client.post('/barcodes/assign', json=barcode).status_code == 201
    assert client.post('/barcodes/assign', json=barcode).status_code == 409
    assert client.get('/barcodes').json()[0]['isPrimary'] is True
    assert client.post('/barcodes/scan', json={'barcodeValue': 'unknown', 'context': 'pos',
                                            'scannedAmount': 1}).status_code == 404
    generated = client.post('/barcodes/generate', json={'sku': 'SKU-A', 'tenantId': 'tenant-1'})
    assert generated.status_code == 201
    assert generated.json()['barcodeValue'].startswith('INV-')
    assert generated.json()['symbology'] == 'Code128'


@pytest.mark.asyncio
async def test_catalog_and_barcodes_allow_same_keys_in_different_tenants(client):
    parent = {'id': 'same-product', 'name': 'Store One'}
    client.post('/inventory/products', json=parent)
    client.post('/inventory/products/same-product/variants', json={'sku': 'SAME-SKU'})
    client.post('/barcodes/assign', json={'sku': 'SAME-SKU', 'barcodeValue': 'same-barcode'})
    client.post('/auth/setup', json={'tenantId': 'other', 'orgName': 'Other', 'adminName': 'Admin',
                                   'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
    token = client.post('/auth/login', json={'tenantId': 'other', 'email': 'admin@example.com',
                                           'password': 'test-password'}).json()['token']
    other = {'Authorization': 'Bearer ' + token}
    assert client.get('/inventory/products', headers=other).json() == []
    assert client.get('/barcodes', headers=other).json() == []
    assert client.post('/inventory/products/same-product/variants', headers=other,
                       json={'sku': 'SAME-SKU'}).status_code == 404
    assert client.post('/inventory/products', headers=other, json={**parent, 'name': 'Store Two'}).status_code == 201
    assert client.post('/inventory/products/same-product/variants', headers=other,
                       json={'sku': 'SAME-SKU'}).status_code == 201
    assert client.post('/barcodes/assign', headers=other,
                       json={'sku': 'SAME-SKU', 'barcodeValue': 'same-barcode'}).status_code == 201
    assert client.get('/inventory/products').json()[0]['name'] == 'Store One'
    assert client.get('/inventory/products', headers=other).json()[0]['name'] == 'Store Two'
