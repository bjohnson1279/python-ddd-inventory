from unittest.mock import MagicMock
import pytest
from src.infrastructure.integrations.omnichannel import (
    SalesChannelAdapter,
    ShopifyAdapter,
    AmazonSPAdapter,
    OmnichannelFramework,
)

def test_sales_channel_adapter_is_abstract():
    with pytest.raises(TypeError):
        SalesChannelAdapter()

def test_sales_channel_adapter_subclass_requires_abstract_methods():
    class IncompleteAdapter(SalesChannelAdapter):
        pass

    with pytest.raises(TypeError):
        IncompleteAdapter()

def test_shopify_adapter_sync_inventory_raises_not_implemented():
    adapter = ShopifyAdapter()
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.sync_inventory("SKU-123", 10)
    assert "Direct provider adapter is not implemented" in str(exc_info.value)

def test_shopify_adapter_ingest_orders_raises_not_implemented():
    adapter = ShopifyAdapter()
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.ingest_orders()
    assert "Provider polling is not implemented" in str(exc_info.value)

def test_amazon_sp_adapter_sync_inventory_raises_not_implemented():
    adapter = AmazonSPAdapter()
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.sync_inventory("SKU-456", 25)
    assert "Direct provider adapter is not implemented" in str(exc_info.value)

def test_amazon_sp_adapter_ingest_orders_raises_not_implemented():
    adapter = AmazonSPAdapter()
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.ingest_orders()
    assert "Provider polling is not implemented" in str(exc_info.value)

def test_omnichannel_framework_initialization():
    framework = OmnichannelFramework()
    assert "shopify" in framework.adapters
    assert "amazon" in framework.adapters
    assert isinstance(framework.adapters["shopify"], ShopifyAdapter)
    assert isinstance(framework.adapters["amazon"], AmazonSPAdapter)

def test_omnichannel_framework_sync_all_channels_with_default_adapters():
    framework = OmnichannelFramework()
    with pytest.raises(NotImplementedError):
        framework.sync_all_channels("SKU-999", 5)

def test_omnichannel_framework_sync_all_channels_calls_all_adapters():
    framework = OmnichannelFramework()
    mock_shopify = MagicMock(spec=SalesChannelAdapter)
    mock_amazon = MagicMock(spec=SalesChannelAdapter)
    framework.adapters = {
        "shopify": mock_shopify,
        "amazon": mock_amazon,
    }

    framework.sync_all_channels("SKU-999", 15)

    mock_shopify.sync_inventory.assert_called_once_with("SKU-999", 15)
    mock_amazon.sync_inventory.assert_called_once_with("SKU-999", 15)
