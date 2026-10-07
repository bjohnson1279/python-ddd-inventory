import logging

from src.infrastructure.shopify.shopify_client import ShopifyClient

logger = logging.getLogger(__name__)

class ShopifyInventoryPublisher:
    def __init__(self, shopify_client: ShopifyClient, location_id: str):
        self.shopify_client = shopify_client
        self.location_id = location_id

    async def publish_stock_level(self, sku: str, quantity: int) -> None:
        find_query = """
        query findInventoryItem($query: String!) {
          inventoryItems(first: 1, query: $query) {
            edges {
              node {
                id
              }
            }
          }
        }
        """

        find_data = await self.shopify_client.query(find_query, {"query": f"sku:{sku}"})
        
        edges = find_data.get("inventoryItems", {}).get("edges", [])
        if not edges:
            logger.warning(f"Could not find Shopify inventory item for SKU: {sku}")
            return
            
        inventory_item_id = edges[0]["node"]["id"]

        mutation = """
        mutation inventorySet($input: InventorySetOnHandQuantitiesInput!) {
          inventorySetOnHandQuantities(input: $input) {
            inventoryLevels {
              id
              quantities(names: ["on_hand"]) {
                quantity
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """

        variables = {
            "input": {
                "reason": "correction",
                "setQuantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": self.location_id,
                        "quantity": quantity
                    }
                ]
            }
        }

        result = await self.shopify_client.query(mutation, variables)
        user_errors = result.get("inventorySetOnHandQuantities", {}).get("userErrors", [])
        
        if user_errors:
            raise Exception(f"Shopify mutation errors: {user_errors}")
