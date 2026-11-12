import pytest
import uuid
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.infrastructure.database import Base
from src.infrastructure.retail_alerts import notify, evaluate
from src.infrastructure.report_alert_models import AlertConfig, AlertCondition, AlertNotice
from src.infrastructure.owned_feature_models import NotificationModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.auth.identity import UserModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.procurement_models import ReorderPolicyModel, SupplierModel
from src.infrastructure.cost_models import CostLayerModel


@pytest.fixture
def engine():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    yield test_engine
    Base.metadata.drop_all(test_engine)
    test_engine.dispose()


def test_notify_creates_notification_and_outbox_event(engine):
    tenant = "tenant-notify"
    user_id = "user-notify-1"
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    message = "Inventory received successfully"
    link = "/inventory/items/SKU-1"

    with engine.begin() as conn:
        notif_id = notify(conn, tenant, user_id, message, now, link=link)

        # Check Notification was inserted
        notification = conn.execute(
            select(NotificationModel.__table__).where(NotificationModel.id == notif_id)
        ).mappings().first()
        assert notification is not None
        assert notification["id"] == notif_id
        assert notification["tenant_id"] == tenant
        assert notification["user_id"] == user_id
        assert notification["message"] == message
        assert notification["link"] == link
        assert notification["status"] == "UNREAD"

        # Check Outbox event was inserted
        event = conn.execute(
            select(OutboxEventModel.__table__).where(
                OutboxEventModel.aggregate_type == "Notification",
                OutboxEventModel.aggregate_id == notif_id,
            )
        ).mappings().first()
        assert event is not None
        assert event["event_type"] == "NotificationCreated"
        assert event["status"] == "PENDING"
        assert event["attempts"] == 0
        assert event["sequence"] == 1
        assert event["payload"] == {
            "tenantId": tenant,
            "recipientId": user_id,
            "notificationId": notif_id,
            "message": message,
            "link": link,
        }


def test_notify_without_link(engine):
    tenant = "tenant-notify-2"
    user_id = "user-notify-2"
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    message = "Simple notice"

    with engine.begin() as conn:
        notif_id = notify(conn, tenant, user_id, message, now)

        notification = conn.execute(
            select(NotificationModel.__table__).where(NotificationModel.id == notif_id)
        ).mappings().first()
        assert notification["link"] is None

        event = conn.execute(
            select(OutboxEventModel.__table__).where(
                OutboxEventModel.aggregate_id == notif_id
            )
        ).mappings().first()
        assert event["payload"]["link"] is None


def test_evaluate_no_config(engine):
    with engine.begin() as conn:
        result = evaluate(conn, "tenant-without-config")
        assert result == {"activeConditions": 0, "notificationsCreated": 0}


def test_evaluate_low_stock_detection_and_recipients(engine):
    tenant = "tenant-low-stock"
    sku = "SKU-LOW"
    loc = "LOC-1"

    with engine.begin() as conn:
        # Enable low_stock alerts
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=True, expiry=False, expiry_days=7, version=1
            )
        )
        # Add users: 1 ADMIN, 1 MANAGER, 1 WAREHOUSE_OPERATOR, 1 INACTIVE, 1 OTHER ROLE
        conn.execute(
            UserModel.__table__.insert(),
            [
                {"id": "u-admin", "tenant_id": tenant, "email": "admin@test.com", "password_hash": "h", "name": "Admin", "role": "ADMIN", "is_active": True},
                {"id": "u-mgr", "tenant_id": tenant, "email": "mgr@test.com", "password_hash": "h", "name": "Manager", "role": "MANAGER", "is_active": True},
                {"id": "u-wh", "tenant_id": tenant, "email": "wh@test.com", "password_hash": "h", "name": "Operator", "role": "WAREHOUSE_OPERATOR", "is_active": True},
                {"id": "u-inactive", "tenant_id": tenant, "email": "inact@test.com", "password_hash": "h", "name": "Inactive", "role": "ADMIN", "is_active": False},
                {"id": "u-viewer", "tenant_id": tenant, "email": "viewer@test.com", "password_hash": "h", "name": "Viewer", "role": "VIEWER", "is_active": True},
            ]
        )
        # Active Supplier and Reorder Policy: reorder_point=10
        conn.execute(
            SupplierModel.__table__.insert().values(
                id="sup-1", tenant_id=tenant, name="Supplier 1", is_active=True
            )
        )
        conn.execute(
            ReorderPolicyModel.__table__.insert().values(
                id="pol-1", tenant_id=tenant, sku=sku, location_id=loc, supplier_id="sup-1",
                reorder_point=10, target_stock=30, case_pack=1, lead_time_days=2
            )
        )
        # Stock: qty=8, allocated=2 -> available = 6 <= 10 -> Low stock triggered!
        conn.execute(
            StockModel.__table__.insert().values(
                id="stk-1", tenant_id=tenant, sku=sku, location_id=loc, quantity=8, allocated=2
            )
        )

        res = evaluate(conn, tenant)
        assert res["activeConditions"] == 1
        # 3 active eligible roles notified: ADMIN, MANAGER, WAREHOUSE_OPERATOR
        assert res["notificationsCreated"] == 3

        # Verify condition was created with episode 1
        conditions = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.tenant_id == tenant)
        ).mappings().all()
        assert len(conditions) == 1
        cond = conditions[0]
        assert cond["kind"] == "LOW_STOCK"
        assert cond["active"] is True
        assert cond["episode"] == 1
        assert cond["facts"]["available"] == 6
        assert cond["facts"]["reorderPoint"] == 10

        # Verify notices were created for each recipient
        notices = conn.execute(
            select(AlertNotice.__table__).where(AlertNotice.tenant_id == tenant)
        ).mappings().all()
        assert len(notices) == 3
        for notice in notices:
            assert notice["condition_id"] == cond["id"]
            assert notice["episode"] == 1
            assert notice["resolved"] is False

        # Running evaluate again without changes shouldn't re-notify existing recipients
        res_repeat = evaluate(conn, tenant)
        assert res_repeat["activeConditions"] == 1
        assert res_repeat["notificationsCreated"] == 0


def test_evaluate_low_stock_unusable_quantities(engine):
    tenant = "tenant-unusable"
    sku = "SKU-UNUSABLE"
    loc = "LOC-1"
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=True, expiry=False, expiry_days=7, version=1
            )
        )
        conn.execute(
            UserModel.__table__.insert().values(
                id="u-admin", tenant_id=tenant, email="admin@test.com", password_hash="h",
                name="Admin", role="ADMIN", is_active=True
            )
        )
        conn.execute(
            SupplierModel.__table__.insert().values(
                id="sup-1", tenant_id=tenant, name="Supplier 1", is_active=True
            )
        )
        conn.execute(
            ReorderPolicyModel.__table__.insert().values(
                id="pol-1", tenant_id=tenant, sku=sku, location_id=loc, supplier_id="sup-1",
                reorder_point=10, target_stock=30, case_pack=1, lead_time_days=2
            )
        )
        # Total stock = 15, allocated = 0.
        # But CostLayer has:
        # - quarantined layer with remaining 3
        # - expired layer with remaining 4 (expired before now)
        # Total unusable = 7.
        # Available = max(0, 15 - 0 - 7) = 8 <= 10 -> Low stock!
        conn.execute(
            StockModel.__table__.insert().values(
                id="stk-1", tenant_id=tenant, sku=sku, location_id=loc, quantity=15, allocated=0
            )
        )
        conn.execute(
            CostLayerModel.__table__.insert(),
            [
                {
                    "id": "layer-1", "stock_id": "stk-1", "tenant_id": tenant, "sku": sku,
                    "location_id": loc, "initial_quantity": 3, "remaining_quantity": 3,
                    "unit_cost_cents": 100, "initial_value_cents": 300, "remaining_value_cents": 300,
                    "quarantined": True, "expiration_date": now + timedelta(days=20),
                    "received_at": now - timedelta(days=5),
                },
                {
                    "id": "layer-2", "stock_id": "stk-1", "tenant_id": tenant, "sku": sku,
                    "location_id": loc, "initial_quantity": 4, "remaining_quantity": 4,
                    "unit_cost_cents": 100, "initial_value_cents": 400, "remaining_value_cents": 400,
                    "quarantined": False, "expiration_date": now - timedelta(days=1),
                    "received_at": now - timedelta(days=5),
                },
            ]
        )

        res = evaluate(conn, tenant)
        assert res["activeConditions"] == 1
        cond = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.tenant_id == tenant)
        ).mappings().first()
        assert cond["facts"]["unusableQuantity"] == 7
        assert cond["facts"]["available"] == 8


def test_evaluate_inactive_supplier_ignored(engine):
    tenant = "tenant-inactive-supplier"
    sku = "SKU-INACT"
    loc = "LOC-1"

    with engine.begin() as conn:
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=True, expiry=False, expiry_days=7, version=1
            )
        )
        conn.execute(
            UserModel.__table__.insert().values(
                id="u-admin", tenant_id=tenant, email="admin@test.com", password_hash="h",
                name="Admin", role="ADMIN", is_active=True
            )
        )
        # Inactive supplier
        conn.execute(
            SupplierModel.__table__.insert().values(
                id="sup-inactive", tenant_id=tenant, name="Inactive Supplier", is_active=False
            )
        )
        conn.execute(
            ReorderPolicyModel.__table__.insert().values(
                id="pol-1", tenant_id=tenant, sku=sku, location_id=loc, supplier_id="sup-inactive",
                reorder_point=10, target_stock=30, case_pack=1, lead_time_days=2
            )
        )
        # Stock is 0, but since supplier is inactive, no alert condition generated
        conn.execute(
            StockModel.__table__.insert().values(
                id="stk-1", tenant_id=tenant, sku=sku, location_id=loc, quantity=0, allocated=0
            )
        )

        res = evaluate(conn, tenant)
        assert res["activeConditions"] == 0
        assert res["notificationsCreated"] == 0


def test_evaluate_expiry_near_and_expired(engine):
    tenant = "tenant-expiry"
    sku = "SKU-EXP"
    loc = "LOC-1"
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        # Expiry alerts enabled with expiry_days = 7
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=False, expiry=True, expiry_days=7, version=1
            )
        )
        conn.execute(
            UserModel.__table__.insert().values(
                id="u-admin", tenant_id=tenant, email="admin@test.com", password_hash="h",
                name="Admin", role="ADMIN", is_active=True
            )
        )
        # Layer 1: Already expired (expiration < now)
        conn.execute(
            CostLayerModel.__table__.insert().values(
                id="lay-expired", stock_id="stk-1", tenant_id=tenant, sku=sku, location_id=loc,
                initial_quantity=5, remaining_quantity=5, unit_cost_cents=100,
                initial_value_cents=500, remaining_value_cents=500, quarantined=False,
                expiration_date=now - timedelta(days=2), received_at=now - timedelta(days=10),
                lot_number="LOT-OLD"
            )
        )
        # Layer 2: Near expiry (now < expiration <= now + 7 days)
        conn.execute(
            CostLayerModel.__table__.insert().values(
                id="lay-near", stock_id="stk-1", tenant_id=tenant, sku=sku, location_id=loc,
                initial_quantity=10, remaining_quantity=10, unit_cost_cents=100,
                initial_value_cents=1000, remaining_value_cents=1000, quarantined=False,
                expiration_date=now + timedelta(days=3), received_at=now - timedelta(days=5),
                lot_number="LOT-SOON"
            )
        )
        # Layer 3: Quarantined - should be skipped for expiry alert
        conn.execute(
            CostLayerModel.__table__.insert().values(
                id="lay-quarantined", stock_id="stk-1", tenant_id=tenant, sku=sku, location_id=loc,
                initial_quantity=2, remaining_quantity=2, unit_cost_cents=100,
                initial_value_cents=200, remaining_value_cents=200, quarantined=True,
                expiration_date=now + timedelta(days=1), received_at=now - timedelta(days=5),
                lot_number="LOT-QUAR"
            )
        )
        # Layer 4: Far future expiry - beyond 7 days - should be skipped
        conn.execute(
            CostLayerModel.__table__.insert().values(
                id="lay-far", stock_id="stk-1", tenant_id=tenant, sku=sku, location_id=loc,
                initial_quantity=20, remaining_quantity=20, unit_cost_cents=100,
                initial_value_cents=2000, remaining_value_cents=2000, quarantined=False,
                expiration_date=now + timedelta(days=30), received_at=now - timedelta(days=5),
                lot_number="LOT-FAR"
            )
        )

        # Layer 5: Another layer with the exact same expiry date and lot number as Layer 2
        # to test aggregation of remaining_quantity into existing detected key
        conn.execute(
            CostLayerModel.__table__.insert().values(
                id="lay-near-2", stock_id="stk-1", tenant_id=tenant, sku=sku, location_id=loc,
                initial_quantity=7, remaining_quantity=7, unit_cost_cents=100,
                initial_value_cents=700, remaining_value_cents=700, quarantined=False,
                expiration_date=now + timedelta(days=3), received_at=now - timedelta(days=2),
                lot_number="LOT-SOON"
            )
        )

        res = evaluate(conn, tenant)
        assert res["activeConditions"] == 2
        assert res["notificationsCreated"] == 2

        conditions = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.tenant_id == tenant)
        ).mappings().all()
        kinds = {c["kind"] for c in conditions}
        assert kinds == {"EXPIRED", "NEAR_EXPIRY"}
        near_cond = [c for c in conditions if c["kind"] == "NEAR_EXPIRY"][0]
        # 10 + 7 = 17 aggregated quantity
        assert near_cond["facts"]["quantity"] == 17

        # Also test scoped evaluation with expiry
        res_scoped = evaluate(conn, tenant, scope=(sku, loc))
        assert res_scoped["activeConditions"] == 2


def test_evaluate_condition_resolution_and_reactivation(engine):
    tenant = "tenant-lifecycle"
    sku = "SKU-CYCLE"
    loc = "LOC-1"

    with engine.begin() as conn:
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=True, expiry=False, expiry_days=7, version=1
            )
        )
        conn.execute(
            UserModel.__table__.insert().values(
                id="u-admin", tenant_id=tenant, email="admin@test.com", password_hash="h",
                name="Admin", role="ADMIN", is_active=True
            )
        )
        conn.execute(
            SupplierModel.__table__.insert().values(
                id="sup-1", tenant_id=tenant, name="Supplier 1", is_active=True
            )
        )
        conn.execute(
            ReorderPolicyModel.__table__.insert().values(
                id="pol-1", tenant_id=tenant, sku=sku, location_id=loc, supplier_id="sup-1",
                reorder_point=5, target_stock=20, case_pack=1, lead_time_days=1
            )
        )
        # Quantity = 2 <= 5 -> Low Stock episode 1
        conn.execute(
            StockModel.__table__.insert().values(
                id="stk-1", tenant_id=tenant, sku=sku, location_id=loc, quantity=2, allocated=0
            )
        )

        res1 = evaluate(conn, tenant)
        assert res1["activeConditions"] == 1
        assert res1["notificationsCreated"] == 1

        cond1 = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.tenant_id == tenant)
        ).mappings().first()
        assert cond1["active"] is True
        assert cond1["episode"] == 1

        notice1 = conn.execute(
            select(AlertNotice.__table__).where(AlertNotice.tenant_id == tenant)
        ).mappings().first()
        assert notice1["resolved"] is False

        # Replenish stock: quantity = 10 > 5 -> Condition resolved!
        conn.execute(
            StockModel.__table__.update().where(StockModel.id == "stk-1").values(quantity=10)
        )
        res2 = evaluate(conn, tenant)
        assert res2["activeConditions"] == 0
        assert res2["notificationsCreated"] == 0

        cond2 = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.id == cond1["id"])
        ).mappings().first()
        assert cond2["active"] is False
        assert cond2["episode"] == 1

        notice2 = conn.execute(
            select(AlertNotice.__table__).where(AlertNotice.id == notice1["id"])
        ).mappings().first()
        assert notice2["resolved"] is True

        # Stock drops again: quantity = 1 <= 5 -> Reactivated into episode 2!
        conn.execute(
            StockModel.__table__.update().where(StockModel.id == "stk-1").values(quantity=1)
        )
        res3 = evaluate(conn, tenant)
        assert res3["activeConditions"] == 1
        assert res3["notificationsCreated"] == 1

        cond3 = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.id == cond1["id"])
        ).mappings().first()
        assert cond3["active"] is True
        assert cond3["episode"] == 2


def test_evaluate_with_scope(engine):
    tenant = "tenant-scope"
    sku1 = "SKU-1"
    sku2 = "SKU-2"
    loc = "LOC-1"

    with engine.begin() as conn:
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=True, expiry=False, expiry_days=7, version=1
            )
        )
        conn.execute(
            UserModel.__table__.insert().values(
                id="u-admin", tenant_id=tenant, email="admin@test.com", password_hash="h",
                name="Admin", role="ADMIN", is_active=True
            )
        )
        conn.execute(
            SupplierModel.__table__.insert().values(
                id="sup-1", tenant_id=tenant, name="Supplier 1", is_active=True
            )
        )
        conn.execute(
            ReorderPolicyModel.__table__.insert(),
            [
                {"id": "pol-1", "tenant_id": tenant, "sku": sku1, "location_id": loc, "supplier_id": "sup-1", "reorder_point": 10, "target_stock": 20, "case_pack": 1, "lead_time_days": 1},
                {"id": "pol-2", "tenant_id": tenant, "sku": sku2, "location_id": loc, "supplier_id": "sup-1", "reorder_point": 10, "target_stock": 20, "case_pack": 1, "lead_time_days": 1},
            ]
        )
        conn.execute(
            StockModel.__table__.insert(),
            [
                {"id": "stk-1", "tenant_id": tenant, "sku": sku1, "location_id": loc, "quantity": 2, "allocated": 0},
                {"id": "stk-2", "tenant_id": tenant, "sku": sku2, "location_id": loc, "quantity": 2, "allocated": 0},
            ]
        )

        # Scoped evaluation for sku1 only
        res = evaluate(conn, tenant, scope=(sku1, loc))
        assert res["activeConditions"] == 1
        assert res["notificationsCreated"] == 1

        conditions = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.tenant_id == tenant)
        ).mappings().all()
        assert len(conditions) == 1
        assert conditions[0]["facts"]["sku"] == sku1


def test_stock_movement_after_insert_event_triggers_evaluate(engine):
    tenant = "tenant-event"
    sku = "SKU-EVENT"
    loc = "LOC-EVENT"

    with engine.begin() as conn:
        conn.execute(
            AlertConfig.__table__.insert().values(
                tenant_id=tenant, low_stock=True, expiry=False, expiry_days=7, version=1
            )
        )
        conn.execute(
            UserModel.__table__.insert().values(
                id="u-admin", tenant_id=tenant, email="admin@test.com", password_hash="h",
                name="Admin", role="ADMIN", is_active=True
            )
        )
        conn.execute(
            SupplierModel.__table__.insert().values(
                id="sup-1", tenant_id=tenant, name="Supplier 1", is_active=True
            )
        )
        conn.execute(
            ReorderPolicyModel.__table__.insert().values(
                id="pol-1", tenant_id=tenant, sku=sku, location_id=loc, supplier_id="sup-1",
                reorder_point=10, target_stock=20, case_pack=1, lead_time_days=1
            )
        )
        conn.execute(
            StockModel.__table__.insert().values(
                id="stk-1", tenant_id=tenant, sku=sku, location_id=loc, quantity=3, allocated=0
            )
        )

    # Use ORM Session so that mapper events on StockMovementModel are triggered
    with Session(engine) as session:
        movement = StockMovementModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            actor_id="u-admin",
            sku=sku,
            location_id=loc,
            amount=1,
            operation="RECEIVE",
            request={"sku": sku, "amount": 1},
            result={"status": "ok"},
            mutation_id=str(uuid.uuid4()),
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(movement)
        session.commit()

    with engine.connect() as conn:
        conditions = conn.execute(
            select(AlertCondition.__table__).where(AlertCondition.tenant_id == tenant)
        ).mappings().all()
        # Verify evaluate was called by the event listener and created the LOW_STOCK condition
        assert len(conditions) == 1
        assert conditions[0]["kind"] == "LOW_STOCK"
