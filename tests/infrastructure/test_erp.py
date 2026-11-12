import pytest
from unittest.mock import MagicMock
from src.infrastructure.integrations.erp import (
    ERPAdapter,
    NetSuiteAdapter,
    QuickBooksAdapter,
    LogisticsAdapter,
    FedExAdapter,
)


def test_erp_adapter_is_abstract():
    with pytest.raises(TypeError, match="Can't instantiate abstract class ERPAdapter"):
        ERPAdapter()
    # Test abstract method body execution directly
    assert ERPAdapter.post_journal_entry(None, {}) is None


def test_logistics_adapter_is_abstract():
    with pytest.raises(TypeError, match="Can't instantiate abstract class LogisticsAdapter"):
        LogisticsAdapter()
    # Test abstract method body execution directly
    assert LogisticsAdapter.generate_shipping_label(None, "ORD-1") is None


def test_concrete_erp_adapter_implementation():
    class CustomERPAdapter(ERPAdapter):
        def post_journal_entry(self, entry: dict) -> bool:
            return bool(entry)

    adapter = CustomERPAdapter()
    assert adapter.post_journal_entry({"memo": "Test Entry"}) is True
    assert adapter.post_journal_entry({}) is False


def test_concrete_logistics_adapter_implementation():
    class CustomLogisticsAdapter(LogisticsAdapter):
        def generate_shipping_label(self, order_id: str) -> str:
            return f"LABEL-{order_id}"

    adapter = CustomLogisticsAdapter()
    assert adapter.generate_shipping_label("ORD-123") == "LABEL-ORD-123"


def test_netsuite_adapter_inheritance_and_not_implemented():
    adapter = NetSuiteAdapter()
    assert isinstance(adapter, ERPAdapter)
    with pytest.raises(
        NotImplementedError,
        match="Direct provider adapter is not implemented; use a configured event destination",
    ):
        adapter.post_journal_entry({"amount": 100})


def test_quickbooks_adapter_inheritance_and_not_implemented():
    adapter = QuickBooksAdapter()
    assert isinstance(adapter, ERPAdapter)
    with pytest.raises(
        NotImplementedError,
        match="Direct provider adapter is not implemented; use a configured event destination",
    ):
        adapter.post_journal_entry({"amount": 250})


def test_fedex_adapter_inheritance_and_not_implemented():
    adapter = FedExAdapter()
    assert isinstance(adapter, LogisticsAdapter)
    with pytest.raises(
        NotImplementedError,
        match="Shipping label purchase is not implemented",
    ):
        adapter.generate_shipping_label("ORD-999")


@pytest.mark.parametrize(
    "adapter_cls",
    [NetSuiteAdapter, QuickBooksAdapter],
)
def test_erp_adapters_edge_case_inputs(adapter_cls):
    adapter = adapter_cls()
    with pytest.raises(NotImplementedError):
        adapter.post_journal_entry({})
    with pytest.raises(NotImplementedError):
        adapter.post_journal_entry({"complex": {"nested": [1, 2, 3]}})


@pytest.mark.parametrize("order_id", ["", "   ", "SPECIAL-#$!*&", "ORD-🚀"])
def test_fedex_adapter_edge_case_order_ids(order_id):
    adapter = FedExAdapter()
    with pytest.raises(NotImplementedError):
        adapter.generate_shipping_label(order_id)


def test_mocking_erp_adapter_interface():
    mock_adapter = MagicMock(spec=ERPAdapter)
    mock_adapter.post_journal_entry.return_value = True

    result = mock_adapter.post_journal_entry({"account": "1000", "debit": 50})
    assert result is True
    mock_adapter.post_journal_entry.assert_called_once_with({"account": "1000", "debit": 50})


def test_mocking_logistics_adapter_interface():
    mock_logistics = MagicMock(spec=LogisticsAdapter)
    mock_logistics.generate_shipping_label.return_value = "https://shipping.labels/mock-123"

    label = mock_logistics.generate_shipping_label("ORD-456")
    assert label == "https://shipping.labels/mock-123"
    mock_logistics.generate_shipping_label.assert_called_once_with("ORD-456")
