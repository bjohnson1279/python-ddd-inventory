import pytest
from src.presentation.graphql import generate_graphql_schema

def test_generate_graphql_schema_validation():
    with pytest.raises(ValueError, match="Invalid configuration: filepath validation failed."):
        generate_graphql_schema('../schema.graphql')

    with pytest.raises(ValueError, match="Invalid configuration: filepath validation failed."):
        generate_graphql_schema('/etc/passwd')

    with pytest.raises(ValueError, match="Invalid configuration: filepath validation failed."):
        generate_graphql_schema('schema.graphql;')
