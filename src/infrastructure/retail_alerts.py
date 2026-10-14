"""Opt-in alert episodes participate in stock producers' existing transaction."""
import json
import hashlib
import uuid
from datetime import timedelta
from sqlalchemy import select, update, event
from src.infrastructure.report_alert_models import AlertConfig, AlertCondition as Condition, AlertNotice as Notice
from src.infrastructure.owned_feature_models import NotificationModel as Notification
from src.infrastructure.auth.identity import UserModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.procurement_models import ReorderPolicyModel, SupplierModel
from src.infrastructure.messaging.models import OutboxEventModel, allocate_sequence
from src.infrastructure.financial_posting import database_now
from src.domain.product.layer_costing import utc


def notify(connection, tenant, user, message, now, *, link=None):
    identifier = str(uuid.uuid4())
    connection.execute(Notification.__table__.insert().values(id=identifier, tenant_id=tenant, user_id=user,
        message=message, link=link, status='UNREAD', created_at=now))
    stream, sequence = allocate_sequence(connection, tenant, 'Notification', identifier)
    connection.execute(OutboxEventModel.__table__.insert().values(id=str(uuid.uuid4()), aggregate_type='Notification',
        aggregate_id=identifier, event_type='NotificationCreated', payload={'tenantId': tenant, 'recipientId': user,
        'notificationId': identifier, 'message': message, 'link': link}, created_at=now,
        status='PENDING', attempts=0, cycle_attempts=0, version=0, stream_key=stream, sequence=sequence))
    return identifier


def evaluate(connection, tenant, scope=None):
    config = connection.execute(select(AlertConfig.__table__).where(AlertConfig.tenant_id == tenant)).mappings().first()
    if not config:
        return {'activeConditions': 0, 'notificationsCreated': 0}
    now, detected = database_now(connection), {}
    if config['low_stock']:
        policies = select(ReorderPolicyModel.__table__).join(SupplierModel, SupplierModel.id == ReorderPolicyModel.supplier_id).where(
            ReorderPolicyModel.tenant_id == tenant, SupplierModel.tenant_id == tenant, SupplierModel.is_active.is_(True))
        if scope:
            policies = policies.where(ReorderPolicyModel.sku == scope[0], ReorderPolicyModel.location_id == scope[1])
        stocks = {(r['sku'], r['location_id']): r for r in connection.execute(select(StockModel.__table__).where(StockModel.tenant_id == tenant)).mappings()}
        blocked = {}
        for layer in connection.execute(select(CostLayerModel.__table__).where(CostLayerModel.tenant_id == tenant, CostLayerModel.remaining_quantity > 0)).mappings():
            if layer['quarantined'] or layer['expiration_date'] and utc(layer['expiration_date']) <= now:
                key = (layer['sku'], layer['location_id'])
                blocked[key] = blocked.get(key, 0) + layer['remaining_quantity']
        for policy in connection.execute(policies).mappings():
            stock = stocks.get((policy['sku'], policy['location_id']))
            unusable = blocked.get((policy['sku'], policy['location_id']), 0)
            available = max(0, stock['quantity'] - stock['allocated'] - unusable) if stock else 0
            if available <= policy['reorder_point']:
                facts = {'sku': policy['sku'], 'locationId': policy['location_id'], 'available': available,
                    'reorderPoint': policy['reorder_point'], 'supplierId': policy['supplier_id'], 'unusableQuantity': unusable}
                key = json.dumps(['LOW_STOCK', policy['sku'], policy['location_id']])
                detected[key] = ('LOW_STOCK', facts, f"Low stock: {policy['sku']} at {policy['location_id']}. Available at detection: {available}; reorder point: {policy['reorder_point']}.")
    if config['expiry']:
        query = select(CostLayerModel.__table__).where(CostLayerModel.tenant_id == tenant, CostLayerModel.remaining_quantity > 0,
            CostLayerModel.quarantined.is_(False), CostLayerModel.expiration_date <= now + timedelta(days=config['expiry_days']))
        if scope:
            query = query.where(CostLayerModel.sku == scope[0], CostLayerModel.location_id == scope[1])
        for layer in connection.execute(query).mappings():
            expiration = utc(layer['expiration_date'])
            kind = 'EXPIRED' if expiration <= now else 'NEAR_EXPIRY'
            key = json.dumps([kind, layer['sku'], layer['location_id'], layer['lot_number'], expiration.isoformat()])
            if key in detected:
                detected[key][1]['quantity'] += layer['remaining_quantity']
            else:
                detected[key] = (kind, {'sku': layer['sku'], 'locationId': layer['location_id'], 'lotNumber': layer['lot_number'],
                    'expiresAt': expiration.isoformat(), 'quantity': layer['remaining_quantity']},
                    f"{kind.replace('_', ' ').title()}: {layer['sku']} at {layer['location_id']}, lot {layer['lot_number'] or 'unassigned'}, expiry {expiration.isoformat()}.")
    existing = {r['key']: r for r in connection.execute(select(Condition.__table__).where(Condition.tenant_id == tenant)).mappings()
        if not scope or (r['facts'].get('sku'), r['facts'].get('locationId')) == scope}
    for key, row in existing.items():
        if row['active'] and key not in detected:
            connection.execute(update(Condition).where(Condition.id == row['id'], Condition.tenant_id == tenant).values(active=False, evaluated_at=now))
            connection.execute(update(Notice).where(Notice.tenant_id == tenant, Notice.condition_id == row['id'], Notice.episode == row['episode']).values(resolved=True))
    recipients = list(connection.scalars(select(UserModel.id).where(UserModel.tenant_id == tenant, UserModel.is_active.is_(True),
        UserModel.role.in_(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR']))))
    count = 0
    for key, (kind, facts, message) in detected.items():
        prior = existing.get(key)
        identifier = prior['id'] if prior else hashlib.sha256((tenant + '\0' + key).encode()).hexdigest()
        episode = prior['episode'] + (not prior['active']) if prior else 1
        if prior:
            connection.execute(update(Condition).where(Condition.id == identifier, Condition.tenant_id == tenant).values(
                active=True, episode=episode, facts=facts, evaluated_at=now))
        else:
            connection.execute(Condition.__table__.insert().values(id=identifier, tenant_id=tenant, key=key, kind=kind,
                active=True, episode=episode, facts=facts, evaluated_at=now))
        notified = set(connection.scalars(select(Notification.user_id).join(Notice, Notice.notification_id == Notification.id).where(
            Notice.tenant_id == tenant, Notification.tenant_id == tenant, Notice.condition_id == identifier, Notice.episode == episode)))
        for recipient in recipients:
            if recipient in notified:
                continue
            notification = notify(connection, tenant, recipient, message, now)
            connection.execute(Notice.__table__.insert().values(id=str(uuid.uuid4()), tenant_id=tenant,
                condition_id=identifier, episode=episode, notification_id=notification, resolved=False))
            count += 1
    return {'activeConditions': len(detected), 'notificationsCreated': count}


@event.listens_for(StockMovementModel, 'after_insert')
def evaluate_changed_stock(mapper, connection, movement):
    evaluate(connection, movement.tenant_id, (movement.sku, movement.location_id))
