import strawberry
from strawberry.fastapi import GraphQLRouter
from typing import List

@strawberry.type
class ProductNode:
    id: str
    sku: str
    name: str
    price_amount: str

@strawberry.type
class Query:
    @strawberry.field
    def product(self, id: str) -> ProductNode:
        return ProductNode(id=id, sku="SKU-123", name="Mock Product", price_amount="19.99")

    @strawberry.field
    def products(self) -> List[ProductNode]:
        return [ProductNode(id="1", sku="SKU-123", name="Mock Product", price_amount="19.99")]

schema = strawberry.federation.Schema(query=Query)
graphql_app = GraphQLRouter(schema)

def generate_graphql_schema(filepath: str = "schema.graphql"):
    """
    Automated GraphQL Schema Specification Synchronizer.
    Dumps the Strawberry schema to a .graphql file for frontend synchronizer usage.
    """
    with open(filepath, "w") as f:
        f.write(str(schema))
