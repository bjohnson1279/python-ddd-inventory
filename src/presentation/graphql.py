import strawberry
from strawberry.fastapi import GraphQLRouter
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.product.models import ProductModel
from src.infrastructure.auth.identity import current_tenant


async def graphql_context(request: Request, session: AsyncSession = Depends(get_db_session)):
    return {'request': request, 'session': session}


@strawberry.type
class ProductNode:
    id: str
    sku: str
    name: str
    price_amount: str


def product_node(product):
    return ProductNode(id=product.id, sku=product.sku, name=product.name, price_amount=str(product.price_amount))


@strawberry.type
class Query:
    @strawberry.field
    async def product(self, info: strawberry.Info, id: str) -> ProductNode | None:
        product = await info.context['session'].scalar(select(ProductModel).where(
            ProductModel.id == id, ProductModel.tenant_id == current_tenant(info.context['request'])))
        return product_node(product) if product else None

    @strawberry.field
    async def products(self, info: strawberry.Info) -> list[ProductNode]:
        products = await info.context['session'].scalars(select(ProductModel).where(
            ProductModel.tenant_id == current_tenant(info.context['request'])).order_by(ProductModel.id).limit(100))
        return [product_node(product) for product in products]


schema = strawberry.federation.Schema(query=Query)
graphql_app = GraphQLRouter(schema, context_getter=graphql_context)


def generate_graphql_schema(filepath: str = 'schema.graphql'):
    with open(filepath, 'w') as output:
        output.write(str(schema))
