import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import Column, String, Integer, BigInteger, Boolean, JSON, DateTime, ForeignKey, UniqueConstraint, CheckConstraint

from src.infrastructure.integration_models import (
    IntegrationConnection,
    IntegrationOrder,
    IntegrationReceipt,
    IntegrationDelivery,
    enqueue_deliveries,
    capture_delivery,
)
from src.infrastructure.messaging.models import OutboxEventModel


class TestIntegrationModelsSchema:
    def test_integration_connection_schema(self):
        assert IntegrationConnection.__tablename__ == 'python_integration_connections'
        cols = {c.name: c for c in IntegrationConnection.__table__.columns}
        assert 'id' in cols and cols['id'].primary_key
        assert 'tenant_id' in cols
        assert 'name' in cols
        assert 'provider' in cols
        assert 'origin' in cols
        assert 'location_id' in cols
        assert 'secret_ref' in cols
        assert 'secret_digest' in cols
        assert 'endpoint_ref' in cols
        assert 'topics' in cols
        assert 'active' in cols
        assert 'version' in cols
        assert 'last_number' in cols

        # Check column default generator for id
        assert cols['id'].default is not None
        assert cols['id'].default.is_callable

        # Check constraints
        table_args = IntegrationConnection.__table_args__
        unique_constraints = [arg for arg in table_args if isinstance(arg, UniqueConstraint)]
        check_constraints = [arg for arg in table_args if isinstance(arg, CheckConstraint)]
        assert len(unique_constraints) == 2
        assert len(check_constraints) == 2

    def test_integration_order_schema(self):
        assert IntegrationOrder.__tablename__ == 'python_integration_orders'
        cols = {c.name: c for c in IntegrationOrder.__table__.columns}
        assert 'id' in cols and cols['id'].primary_key
        assert 'tenant_id' in cols
        assert 'connection_id' in cols
        assert 'external_id' in cols
        assert 'delivery_id' in cols
        assert 'request' in cols
        assert 'result' in cols
        assert 'status' in cols
        assert 'version' in cols
        assert 'created_at' in cols

        # Check defaults
        assert cols['id'].default is not None
        assert cols['id'].default.is_callable
        assert cols['created_at'].default is not None
        assert cols['created_at'].default.is_callable

        table_args = IntegrationOrder.__table_args__
        unique_constraints = [arg for arg in table_args if isinstance(arg, UniqueConstraint)]
        check_constraints = [arg for arg in table_args if isinstance(arg, CheckConstraint)]
        assert len(unique_constraints) == 2
        assert len(check_constraints) == 2

    def test_integration_receipt_schema(self):
        assert IntegrationReceipt.__tablename__ == 'python_integration_receipts'
        cols = {c.name: c for c in IntegrationReceipt.__table__.columns}
        assert 'id' in cols and cols['id'].primary_key
        assert 'tenant_id' in cols
        assert 'connection_id' in cols
        assert 'delivery_id' in cols
        assert 'order_id' in cols

        table_args = IntegrationReceipt.__table_args__
        unique_constraints = [arg for arg in table_args if isinstance(arg, UniqueConstraint)]
        assert len(unique_constraints) == 1

    def test_integration_delivery_schema(self):
        assert IntegrationDelivery.__tablename__ == 'python_integration_deliveries'
        cols = {c.name: c for c in IntegrationDelivery.__table__.columns}
        assert 'id' in cols and cols['id'].primary_key
        assert 'tenant_id' in cols
        assert 'connection_id' in cols
        assert 'event_id' in cols
        assert 'number' in cols
        assert 'payload' in cols
        assert 'endpoint_ref' in cols
        assert 'secret_ref' in cols
        assert 'status' in cols
        assert 'attempts' in cols
        assert 'cycle_attempts' in cols
        assert 'version' in cols
        assert 'token' in cols
        assert 'lease_until' in cols
        assert 'next_attempt_at' in cols
        assert 'last_error' in cols
        assert 'external_id' in cols
        assert 'delivered_at' in cols

        assert cols['id'].default is not None
        assert cols['id'].default.is_callable

        table_args = IntegrationDelivery.__table_args__
        unique_constraints = [arg for arg in table_args if isinstance(arg, UniqueConstraint)]
        check_constraints = [arg for arg in table_args if isinstance(arg, CheckConstraint)]
        assert len(unique_constraints) == 2
        assert len(check_constraints) == 2


class TestEnqueueDeliveries:
    def test_enqueue_deliveries_ignores_unsupported_event_types(self):
        mock_connection = MagicMock()
        enqueue_deliveries(mock_connection, 'evt-1', 'tenant-1', 'ProductCreated', {'sku': '123'})
        mock_connection.execute.assert_not_called()
        mock_connection.scalar.assert_not_called()

    def test_enqueue_deliveries_skips_destination_when_topic_not_subscribed(self):
        mock_connection = MagicMock()
        destination = {
            'id': 'conn-1',
            'topics': ['OtherTopic'],
            'endpoint_ref': 'https://sink.example.com',
            'secret_ref': 'SECRET_1',
        }
        mock_connection.execute.return_value.mappings.return_value = [destination]

        enqueue_deliveries(mock_connection, 'evt-1', 'tenant-1', 'StockChanged', {'sku': '123'})

        mock_connection.execute.assert_called_once()
        mock_connection.scalar.assert_not_called()

    def test_enqueue_deliveries_enqueues_when_topic_matches(self):
        mock_connection = MagicMock()
        destination = {
            'id': 'conn-1',
            'topics': ['StockChanged'],
            'endpoint_ref': 'https://sink.example.com',
            'secret_ref': 'SECRET_1',
        }
        mock_connection.execute.return_value.mappings.return_value = [destination]
        mock_connection.scalar.return_value = 42

        event_id = 'evt-100'
        tenant = 'tenant-abc'
        event_type = 'StockChanged'
        payload = {'sku': 'SKU-001', 'qty': 5}

        enqueue_deliveries(mock_connection, event_id, tenant, event_type, payload)

        # 1st execute is select
        assert mock_connection.execute.call_count == 2
        mock_connection.scalar.assert_called_once()

        # Check delivery insertion
        insert_call = mock_connection.execute.call_args_list[1]
        insert_clause = insert_call[0][0]
        params = insert_clause.compile().params
        assert params['tenant_id'] == tenant
        assert params['connection_id'] == 'conn-1'
        assert params['event_id'] == event_id
        assert params['number'] == 42
        assert params['payload'] == {
            'eventId': event_id,
            'tenantId': tenant,
            'type': event_type,
            'data': payload,
        }
        assert params['endpoint_ref'] == 'https://sink.example.com'
        assert params['secret_ref'] == 'SECRET_1'
        assert params['status'] == 'PENDING'
        assert params['attempts'] == 0
        assert params['cycle_attempts'] == 0
        assert params['version'] == 0

    def test_enqueue_deliveries_handles_journal_entry_posted(self):
        mock_connection = MagicMock()
        destination = {
            'id': 'conn-sink',
            'topics': ['JournalEntryPosted'],
            'endpoint_ref': 'https://sink.example.com/journal',
            'secret_ref': 'SECRET_J',
        }
        mock_connection.execute.return_value.mappings.return_value = [destination]
        mock_connection.scalar.return_value = 1

        enqueue_deliveries(mock_connection, 'evt-200', 'tenant-xyz', 'JournalEntryPosted', {'amount': 100})
        assert mock_connection.execute.call_count == 2
        assert mock_connection.scalar.call_count == 1


class TestCaptureDelivery:
    @patch('src.infrastructure.integration_models.enqueue_deliveries')
    def test_capture_delivery_with_tenant_in_dict(self, mock_enqueue):
        mock_row = MagicMock()
        mock_row.id = 'evt-1'
        mock_row.event_type = 'StockChanged'
        mock_row.payload = {'tenantId': 'tenant-test', 'data': 123}
        mock_connection = MagicMock()

        capture_delivery(None, mock_connection, mock_row)

        mock_enqueue.assert_called_once_with(
            mock_connection,
            'evt-1',
            'tenant-test',
            'StockChanged',
            mock_row.payload,
        )

    @patch('src.infrastructure.integration_models.enqueue_deliveries')
    def test_capture_delivery_missing_tenant_id(self, mock_enqueue):
        mock_row = MagicMock()
        mock_row.payload = {'other': 'data'}
        mock_connection = MagicMock()

        capture_delivery(None, mock_connection, mock_row)

        mock_enqueue.assert_not_called()

    @patch('src.infrastructure.integration_models.enqueue_deliveries')
    def test_capture_delivery_non_dict_payload(self, mock_enqueue):
        mock_row = MagicMock()
        mock_row.payload = 'not-a-dict'
        mock_connection = MagicMock()

        capture_delivery(None, mock_connection, mock_row)

        mock_enqueue.assert_not_called()
