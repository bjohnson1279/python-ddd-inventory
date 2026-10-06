import pytest
from unittest.mock import AsyncMock, MagicMock
from src.application.integrations.channel_ingestion_service import ChannelIngestionService


@pytest.mark.asyncio
async def test_ingest_order_aggregates_skus_and_dispatches_concurrently():
    mock_dispatch = MagicMock()
    # Mock execute as an async function
    mock_dispatch.execute = AsyncMock()
    # Ensure execute_batch is not present
    if hasattr(mock_dispatch, 'execute_batch'):
        delattr(mock_dispatch, 'execute_batch')

    service = ChannelIngestionService(
        inventory_repository=None,
        dispatch_use_case=mock_dispatch
    )

    line_items = [
        {"sku": "SKU-1", "quantity": 2},
        {"sku": "SKU-2", "quantity": 1},
        {"sku": "SKU-1", "quantity": 3},
    ]

    await service.ingest_order(
        channel_id="shopify",
        external_order_id="1001",
        line_items=line_items,
        tenant_id="tenant-abc"
    )

    assert mock_dispatch.execute.call_count == 2
    mock_dispatch.execute.assert_any_call(
        sku="SKU-1",
        quantity=5,
        tenant_id="tenant-abc",
        skip_publish_to_channel="shopify"
    )
    mock_dispatch.execute.assert_any_call(
        sku="SKU-2",
        quantity=1,
        tenant_id="tenant-abc",
        skip_publish_to_channel="shopify"
    )


@pytest.mark.asyncio
async def test_ingest_order_uses_execute_batch_when_available():
    mock_dispatch = MagicMock()
    mock_dispatch.execute_batch = AsyncMock()

    service = ChannelIngestionService(
        inventory_repository=None,
        dispatch_use_case=mock_dispatch
    )

    line_items = [
        {"sku": "SKU-A", "quantity": 5},
        {"sku": "SKU-B", "quantity": 10},
    ]

    await service.ingest_order(
        channel_id="amazon",
        external_order_id="2002",
        line_items=line_items,
        tenant_id="tenant-xyz"
    )

    mock_dispatch.execute_batch.assert_called_once_with(
        items=[
            {"sku": "SKU-A", "quantity": 5},
            {"sku": "SKU-B", "quantity": 10},
        ],
        tenant_id="tenant-xyz",
        skip_publish_to_channel="amazon"
    )


@pytest.mark.asyncio
async def test_ingest_order_empty_line_items():
    mock_dispatch = MagicMock()
    mock_dispatch.execute = AsyncMock()

    service = ChannelIngestionService(
        inventory_repository=None,
        dispatch_use_case=mock_dispatch
    )

    await service.ingest_order(
        channel_id="shopify",
        external_order_id="3003",
        line_items=[],
        tenant_id="tenant-abc"
    )

    mock_dispatch.execute.assert_not_called()
