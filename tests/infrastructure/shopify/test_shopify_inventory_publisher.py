import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.infrastructure.shopify.shopify_inventory_publisher import ShopifyInventoryPublisher
from src.infrastructure.shopify.shopify_client import ShopifyClient

@pytest.fixture
def shopify_client_mock():
    return AsyncMock(spec=ShopifyClient)

@pytest.fixture
def publisher(shopify_client_mock):
    return ShopifyInventoryPublisher(shopify_client_mock, "gid://shopify/Location/12345")

@pytest.mark.asyncio
async def test_publish_stock_level_success(publisher, shopify_client_mock):
    # Mock finding the inventory item
    shopify_client_mock.query.side_effect = [
        {
            "inventoryItems": {
                "edges": [{"node": {"id": "gid://shopify/InventoryItem/67890"}}]
            }
        },
        {
            "inventorySetOnHandQuantities": {
                "inventoryLevels": [],
                "userErrors": []
            }
        }
    ]

    await publisher.publish_stock_level("TEST-SKU", 15)

    assert shopify_client_mock.query.call_count == 2
    
    # Check the find query
    find_call = shopify_client_mock.query.call_args_list[0]
    assert "sku:TEST-SKU" in find_call[0][1]["query"]
    
    # Check the mutation
    mutation_call = shopify_client_mock.query.call_args_list[1]
    variables = mutation_call[0][1]
    assert variables["input"]["setQuantities"][0]["inventoryItemId"] == "gid://shopify/InventoryItem/67890"
    assert variables["input"]["setQuantities"][0]["locationId"] == "gid://shopify/Location/12345"
    assert variables["input"]["setQuantities"][0]["quantity"] == 15

@pytest.mark.asyncio
async def test_publish_stock_level_not_found(publisher, shopify_client_mock):
    # Return empty edges for find query
    shopify_client_mock.query.return_value = {
        "inventoryItems": {
            "edges": []
        }
    }

    # Should just return without calling mutation
    await publisher.publish_stock_level("UNKNOWN-SKU", 15)
    assert shopify_client_mock.query.call_count == 1

@pytest.mark.asyncio
async def test_publish_stock_level_mutation_error(publisher, shopify_client_mock):
    shopify_client_mock.query.side_effect = [
        {
            "inventoryItems": {
                "edges": [{"node": {"id": "gid://shopify/InventoryItem/67890"}}]
            }
        },
        {
            "inventorySetOnHandQuantities": {
                "userErrors": [{"message": "Invalid location"}]
            }
        }
    ]

    with pytest.raises(Exception, match="Shopify mutation errors"):
        await publisher.publish_stock_level("TEST-SKU", 15)
