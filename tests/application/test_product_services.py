import pytest
from unittest.mock import AsyncMock
from src.application.product.services import ProductService
from src.domain.product.exceptions import ProductNotFoundError
from src.domain.product.entity import Product

@pytest.fixture
def repo_mock():
    return AsyncMock()

@pytest.fixture
def service(repo_mock):
    return ProductService(repo_mock)

@pytest.mark.asyncio
async def test_create_product_success(service, repo_mock):
    repo_mock.get_by_sku.return_value = None
    product = await service.create_product('NEW-SKU', 'New Product', None, '10.00')
    assert product.sku.value == 'NEW-SKU'
    repo_mock.save.assert_called_once()

@pytest.mark.asyncio
async def test_create_product_duplicate_sku(service, repo_mock):
    repo_mock.get_by_sku.return_value = Product.create('EXIST-SKU', 'Exist', None, '10.00')
    with pytest.raises(ValueError):
        await service.create_product('EXIST-SKU', 'Another', None, '15.00')

@pytest.mark.asyncio
async def test_get_product_not_found(service, repo_mock):
    repo_mock.get_by_id.return_value = None
    with pytest.raises(ProductNotFoundError):
        await service.get_product('bad-id')

