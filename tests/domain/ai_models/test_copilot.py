import pytest
from src.domain.ai_models.copilot import WarehouseDigitalTwin, ConversationalAICopilot


def test_warehouse_digital_twin_simulate_stress_test_nominal():
    twin = WarehouseDigitalTwin()
    result = twin.simulate_stress_test(throughput=100)
    assert isinstance(result, dict)
    assert result["bottlenecks_detected"] == ["Pack Station 3"]
    assert result["max_throughput"] == 80.0


def test_warehouse_digital_twin_simulate_stress_test_zero():
    twin = WarehouseDigitalTwin()
    result = twin.simulate_stress_test(throughput=0)
    assert isinstance(result, dict)
    assert result["bottlenecks_detected"] == ["Pack Station 3"]
    assert result["max_throughput"] == 0.0


def test_warehouse_digital_twin_simulate_stress_test_negative_throughput():
    twin = WarehouseDigitalTwin()
    result = twin.simulate_stress_test(throughput=-50)
    assert isinstance(result, dict)
    assert result["bottlenecks_detected"] == ["Pack Station 3"]
    assert result["max_throughput"] == -40.0


def test_warehouse_digital_twin_simulate_stress_test_large_throughput():
    twin = WarehouseDigitalTwin()
    result = twin.simulate_stress_test(throughput=1000000)
    assert result["max_throughput"] == 800000.0


def test_conversational_copilot_stock_query_exact_match():
    copilot = ConversationalAICopilot()
    response = copilot.process_natural_language_query("stock")
    assert response == "You have 50 units remaining for the most popular SKU."


def test_conversational_copilot_stock_query_case_insensitivity():
    copilot = ConversationalAICopilot()
    queries = [
        "What is the current STOCK level?",
        "check Stock for SKU-123",
        "IS IT IN StOcK?",
    ]
    for query in queries:
        assert (
            copilot.process_natural_language_query(query)
            == "You have 50 units remaining for the most popular SKU."
        )


def test_conversational_copilot_default_query_fallback():
    copilot = ConversationalAICopilot()
    general_queries = [
        "Hello, who are you?",
        "Where is aisle 4 located?",
        "Generate a report for yesterday",
    ]
    for query in general_queries:
        assert (
            copilot.process_natural_language_query(query)
            == "I am an AI assistant. How can I help you manage your warehouse?"
        )


def test_conversational_copilot_empty_and_whitespace_query():
    copilot = ConversationalAICopilot()
    for empty_query in ["", "   ", "\t\n"]:
        assert (
            copilot.process_natural_language_query(empty_query)
            == "I am an AI assistant. How can I help you manage your warehouse?"
        )
