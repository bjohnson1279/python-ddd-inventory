import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit
import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.infrastructure.integration_models import IntegrationConnection as Connection, IntegrationOrder as Order, IntegrationDelivery as Delivery, IntegrationReceipt as Receipt
from src.infrastructure.financial_posting import database_now
from src.application.costing_service import locked_config
from src.application.stock_service import StockService, StockConflict
from src.domain.product.layer_costing import utc


def setting(reference, *, secret=False):
    if not re.fullmatch(r'INTEGRATION_[A-Z0-9_]{1,100}', reference or ''):
        raise ValueError('Use an INTEGRATION_ environment variable reference')
    value = os.getenv(reference, '')
    if not value or secret and len(value.encode()) < 32:
        raise ValueError('Integration setting is missing or its signing secret is too short')
    return value


def endpoint(reference):
    value = setting(reference)
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError('Operator-configured delivery endpoint must be HTTPS without credentials or a fragment')
    return value


def allowed_settings(tenant, references):
    try:
        allowed = json.loads(os.getenv('INTEGRATION_TENANT_SETTINGS', '{}')).get(tenant, [])
    except (ValueError, AttributeError):
        raise ValueError('Operator integration setting assignments are invalid')
    if not isinstance(allowed, list) or any(reference not in allowed for reference in references if reference):
        raise ValueError('Integration settings must be assigned to this tenant by the operator')


def connection_dto(row):
    return {'id': row.id, 'name': row.name, 'provider': row.provider, 'origin': row.origin,
        'locationId': row.location_id, 'active': row.active, 'version': row.version,
        'topics': row.topics, 'webhookPath': f'/webhooks/integrations/{row.tenant_id}/{row.id}/orders' if row.provider != 'EVENT_SINK' else None}


def delivery_dto(row):
    return {'id': row.id, 'connectionId': row.connection_id, 'eventId': row.event_id, 'number': row.number,
        'type': row.payload['type'], 'status': row.status, 'attempts': row.attempts, 'cycleAttempts': row.cycle_attempts, 'version': row.version,
        'lastError': row.last_error, 'externalId': row.external_id,
        'deliveredAt': utc(row.delivered_at).isoformat() if row.delivered_at else None}


async def owned(session, model, tenant, identifier):
    row = await session.scalar(select(model).where(model.id == identifier, model.tenant_id == tenant))
    if not row:
        raise LookupError('Integration record not found')
    return row


def parse_order(raw):
    try:
        payload = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (ValueError, UnicodeDecodeError):
        raise ValueError('Invalid order JSON')
    if not isinstance(payload, dict) or type(payload.get('id')) not in (str, int) or not str(payload['id']).strip() or len(str(payload['id'])) > 100:
        raise ValueError('An external order ID is required')
    items = payload.get('line_items')
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('Orders require 1–100 SKU lines')
    quantities = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('sku'), str) or not item['sku'].strip() or len(item['sku']) > 100:
            raise ValueError('Every line requires a local SKU; missing mappings cannot be silently skipped')
        quantity = item.get('quantity')
        if type(quantity) is not int or not 1 <= quantity <= 2147483647:
            raise ValueError('Every order quantity must be a positive integer')
        quantities[item['sku']] = quantities.get(item['sku'], 0) + quantity
        if quantities[item['sku']] > 2147483647:
            raise ValueError('Order quantity exceeds the supported limit')
    return payload, {'externalId': str(payload['id']), 'lines': [{'sku': sku, 'quantity': quantity} for sku, quantity in sorted(quantities.items())]}


async def ingest(session, tenant, connection_id, raw, headers):
    """The URL is a routing hint. Only the owned connection signature authorizes writes."""
    if session.bind.dialect.name == 'postgresql':
        from src.application.workers.outbox_worker import set_worker_tenant
        await set_worker_tenant(session, tenant)
    row = await owned(session, Connection, tenant, connection_id)
    if not row.active or row.provider not in ('SHOPIFY', 'WOOCOMMERCE'):
        raise PermissionError('Invalid webhook connection or signature')
    # WooCommerce's documented initial reachability ping is unsigned form data.
    # It acknowledges only this URL; it never reserves inventory or creates an order.
    if row.provider == 'WOOCOMMERCE' and headers.get('content-type', '').split(';')[0] == 'application/x-www-form-urlencoded' and re.fullmatch(rb'webhook_id=[0-9]{1,20}', raw):
        await session.rollback()
        return {'status': 'PING_ONLY'}
    secret = setting(row.secret_ref, secret=True).encode()
    if hashlib.sha256(secret).hexdigest() != row.secret_digest:
        raise PermissionError('Connection signing key changed; register its replacement explicitly')
    if row.provider == 'SHOPIFY':
        signature, delivery = headers.get('x-shopify-hmac-sha256'), headers.get('x-shopify-webhook-id')
        origin, topic = headers.get('x-shopify-shop-domain'), headers.get('x-shopify-topic')
        expected_topic = 'orders/create'
    else:
        signature, delivery = headers.get('x-wc-webhook-signature'), headers.get('x-wc-webhook-delivery-id')
        origin, topic = headers.get('x-wc-webhook-source'), headers.get('x-wc-webhook-topic')
        expected_topic = 'order.created'
    expected = base64.b64encode(hmac.new(secret, raw, hashlib.sha256).digest()).decode()
    if not signature or not hmac.compare_digest(expected.encode('ascii'), signature.encode('utf-8')) or origin != row.origin:
        raise PermissionError('Invalid webhook connection or signature')
    if topic != expected_topic or not delivery or len(delivery) > 100:
        raise ValueError('Expected order topic and a delivery ID')
    payload, normalized = parse_order(raw)
    from src.infrastructure.auth.security import tenant_selectors
    tenant_selectors(payload, tenant)
    config = await locked_config(session, tenant)
    await session.refresh(row)
    if not row.active:
        raise PermissionError('Invalid webhook connection or signature')
    receipt = await session.scalar(select(Receipt).where(Receipt.tenant_id == tenant, Receipt.connection_id == row.id, Receipt.delivery_id == delivery))
    prior = await owned(session, Order, tenant, receipt.order_id) if receipt else await session.scalar(select(Order).where(
        Order.tenant_id == tenant, Order.connection_id == row.id, Order.external_id == normalized['externalId']))
    if prior:
        if prior.request != normalized:
            raise StockConflict('External order or delivery ID was reused with changed stock lines')
        result = prior.result
        if not receipt:
            session.add(Receipt(id=str(uuid.uuid4()), tenant_id=tenant, connection_id=row.id, delivery_id=delivery, order_id=prior.id))
            await session.commit()
        else:
            await session.rollback()
        return result
    identifier, results = str(uuid.uuid4()), []
    for index, line in enumerate(normalized['lines']):
        result = await StockService(session, tenant, 'integration:' + row.id).mutate('allocate', line['sku'], row.location_id,
            line['quantity'], mutation_id='channel:' + identifier + ':' + str(index), commit=False, config=config,
            reference={'integrationId': row.id, 'externalOrderId': normalized['externalId']})
        # Public webhook acknowledgements never disclose stock quantities or acquisition costs.
        results.append({'sku': line['sku'], 'quantity': line['quantity'], 'stockVersion': result['version']})
    result = {'id': identifier, 'externalOrderId': normalized['externalId'], 'status': 'RESERVED', 'version': 0, 'lines': results}
    session.add(Order(id=identifier, tenant_id=tenant, connection_id=row.id, external_id=normalized['externalId'],
        delivery_id=delivery, request=normalized, result=result))
    await session.flush()
    session.add(Receipt(id=str(uuid.uuid4()), tenant_id=tenant, connection_id=row.id, delivery_id=delivery, order_id=identifier))
    await session.commit()
    return result


async def dispatch_head(session, tenant, connection_id, *, transport=None):
    """Lease one ordered head, release DB locks during HTTPS, then fence its acknowledgement."""
    await locked_config(session, tenant)
    connection = await owned(session, Connection, tenant, connection_id)
    if not connection.active:
        await session.rollback()
        return None
    row = await session.scalar(select(Delivery).where(Delivery.tenant_id == tenant, Delivery.connection_id == connection_id,
        Delivery.status != 'DELIVERED').order_by(Delivery.number).limit(1))
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    if not row or row.status == 'FAILED' or row.status == 'SENDING' and row.lease_until and utc(row.lease_until) > now or row.next_attempt_at and utc(row.next_attempt_at) > now:
        await session.rollback()
        return None
    token = str(uuid.uuid4())
    row.status, row.token, row.lease_until = 'SENDING', token, now + timedelta(seconds=60)
    row.attempts += 1
    row.cycle_attempts += 1
    row.version += 1
    identifier, payload, endpoint_ref, secret_ref = row.id, row.payload, row.endpoint_ref, row.secret_ref
    await session.commit()
    error, remote_id = None, None
    try:
        raw = json.dumps(payload, separators=(',', ':'), sort_keys=True, allow_nan=False).encode()
        signature = hmac.new(setting(secret_ref, secret=True).encode(), raw, hashlib.sha256).hexdigest()

        target_url = endpoint(endpoint_ref)

        async def make_request(target_url, raw, identifier, signature, transport):
            if transport is None:
                import socket
                import ipaddress
                import asyncio
                from urllib.parse import urlparse
                parsed = urlparse(target_url)
                try:
                    loop = asyncio.get_running_loop()
                    addr_infos = await loop.getaddrinfo(parsed.hostname, None)
                    resolved_ip = None
                    for addr in addr_infos:
                        ip_str = addr[4][0]
                        ip_obj = ipaddress.ip_address(ip_str)
                        if (ip_obj.is_private or ip_obj.is_loopback or
                            ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast):
                            raise ValueError('SSRF blocked: local or internal IP addresses are not allowed')
                        resolved_ip = ip_str
                    if resolved_ip is None:
                        raise ValueError('Invalid Webhook URL: unable to resolve hostname')
                except socket.gaierror:
                    raise ValueError('Invalid Webhook URL: unable to resolve hostname')

                # TOCTOU Mitigation: Perform SSRF validation immediately before HTTP request.
                # However, to avoid breaking TLS, we still request the URL directly and rely on the
                # immediate prior validation.
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False, transport=transport) as client:
                return await client.post(target_url, content=raw, headers={'Content-Type': 'application/json',
                    'Idempotency-Key': identifier, 'X-Inventory-Delivery-Id': identifier, 'X-Inventory-Signature': signature})

        response = await make_request(target_url, raw, identifier, signature, transport)
        if response.status_code < 200 or response.status_code >= 300:
            raise ValueError('RemoteHTTP' + str(response.status_code))
        acknowledgement = response.json()
        if not isinstance(acknowledgement, dict) or acknowledgement.get('eventId') != payload['eventId'] or acknowledgement.get('accepted') is not True:
            raise ValueError('InvalidAcknowledgement')
        remote_id = acknowledgement.get('externalId')
        if not isinstance(remote_id, str) or not 1 <= len(remote_id) <= 200:
            raise ValueError('MissingExternalReference')
    except Exception as exc:
        # Never persist remote bodies, secret values, URLs or exception text.
        error = str(exc) if type(exc) is ValueError and re.fullmatch(r'RemoteHTTP\d{3}|InvalidAcknowledgement|MissingExternalReference|SSRF blocked: local or internal IP addresses are not allowed|Invalid Webhook URL: unable to resolve hostname', str(exc)) else type(exc).__name__
    if session.bind.dialect.name == 'postgresql':
        from src.application.workers.outbox_worker import set_worker_tenant
        await set_worker_tenant(session, tenant)
    await locked_config(session, tenant)
    row = await owned(session, Delivery, tenant, identifier)
    if row.token != token:
        await session.rollback()
        return None
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    row.token, row.lease_until = None, None
    row.version += 1
    if error:
        row.status = 'FAILED' if row.cycle_attempts >= 3 else 'PENDING'
        row.last_error, row.next_attempt_at = error, now + timedelta(seconds=min(300, 5 * 2 ** row.cycle_attempts))
    else:
        row.status, row.external_id, row.delivered_at, row.last_error = 'DELIVERED', remote_id, now, None
        row.next_attempt_at = None
    await session.commit()
    return delivery_dto(row)
