import httpx
from typing import Any, Dict, Optional

class ShopifyClient:
    def __init__(self, shop_url: str, access_token: str):
        self.shop_url = shop_url
        self.access_token = access_token
        self.api_version = "2024-04"

    @property
    def graphql_url(self) -> str:
        return f"https://{self.shop_url}/admin/api/{self.api_version}/graphql.json"

    async def query(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Any:
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": self.access_token,
        }
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        async with httpx.AsyncClient() as client:
            response = await client.post(self.graphql_url, headers=headers, json=payload)
            
            if not response.is_success:
                error_text = response.text
                raise Exception(f"Shopify API error ({response.status_code}): {error_text}")

            json_data = response.json()
            if "errors" in json_data and json_data["errors"]:
                raise Exception(f"Shopify GraphQL errors: {json_data['errors']}")

            return json_data.get("data")
