import pytest
import httpx
from unittest.mock import patch, MagicMock, AsyncMock
from src.infrastructure.shopify.shopify_client import ShopifyClient

@pytest.fixture
def client():
    return ShopifyClient("test-shop.myshopify.com", "test_token")

@pytest.mark.asyncio
async def test_graphql_url(client):
    assert client.graphql_url == "https://test-shop.myshopify.com/admin/api/2024-04/graphql.json"

@pytest.mark.asyncio
@patch('httpx.AsyncClient.post')
async def test_query_success(mock_post, client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"data": {"shop": {"name": "Test Shop"}}}
    mock_post.return_value = mock_response

    result = await client.query("query { shop { name } }")
    assert result == {"shop": {"name": "Test Shop"}}
    
    # Verify headers
    args, kwargs = mock_post.call_args
    assert kwargs['headers']['X-Shopify-Access-Token'] == "test_token"
    assert kwargs['headers']['Content-Type'] == "application/json"

@pytest.mark.asyncio
@patch('httpx.AsyncClient.post')
async def test_query_http_error(mock_post, client):
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_post.return_value = mock_response

    with pytest.raises(Exception, match="Shopify API error \\(500\\)"):
        await client.query("query { shop { name } }")

@pytest.mark.asyncio
@patch('httpx.AsyncClient.post')
async def test_query_graphql_error(mock_post, client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"errors": [{"message": "Syntax error"}]}
    mock_post.return_value = mock_response

    with pytest.raises(Exception, match="Shopify GraphQL errors"):
        await client.query("invalid query")
