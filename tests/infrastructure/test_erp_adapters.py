import pytest
from src.infrastructure.integrations.erp import (
    ERPAdapter,
    NetSuiteAdapter,
    QuickBooksAdapter,
    LogisticsAdapter,
    FedExAdapter,
)


def test_erp_adapter_is_abstract():
    """ERPAdapter cannot be instantiated directly because post_journal_entry is abstract."""
    with pytest.raises(TypeError) as excinfo:
        ERPAdapter()
    assert "Can't instantiate abstract class ERPAdapter" in str(excinfo.value)


def test_netsuite_adapter_inheritance():
    """NetSuiteAdapter must inherit from ERPAdapter."""
    assert issubclass(NetSuiteAdapter, ERPAdapter)
    adapter = NetSuiteAdapter()
    assert isinstance(adapter, ERPAdapter)


def test_netsuite_adapter_post_journal_entry_raises():
    """NetSuiteAdapter.post_journal_entry raises NotImplementedError with prescriptive message."""
    adapter = NetSuiteAdapter()
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.post_journal_entry({"debit": 100, "credit": 100})
    assert str(excinfo.value) == "Direct provider adapter is not implemented; use a configured event destination"

    # Also check with empty dict
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.post_journal_entry({})
    assert str(excinfo.value) == "Direct provider adapter is not implemented; use a configured event destination"


def test_quickbooks_adapter_inheritance():
    """QuickBooksAdapter must inherit from ERPAdapter."""
    assert issubclass(QuickBooksAdapter, ERPAdapter)
    adapter = QuickBooksAdapter()
    assert isinstance(adapter, ERPAdapter)


def test_quickbooks_adapter_post_journal_entry_raises():
    """QuickBooksAdapter.post_journal_entry raises NotImplementedError with prescriptive message."""
    adapter = QuickBooksAdapter()
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.post_journal_entry({"account": "1000", "amount": 250.0})
    assert str(excinfo.value) == "Direct provider adapter is not implemented; use a configured event destination"

    # Also check with empty dict
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.post_journal_entry({})
    assert str(excinfo.value) == "Direct provider adapter is not implemented; use a configured event destination"


def test_concrete_erp_adapter_implementation():
    """A custom concrete subclass implementing post_journal_entry can be instantiated and used."""
    class CustomMockERPAdapter(ERPAdapter):
        def __init__(self):
            self.posted_entries = []

        def post_journal_entry(self, entry):
            self.posted_entries.append(entry)
            return True

    adapter = CustomMockERPAdapter()
    assert isinstance(adapter, ERPAdapter)
    sample_entry = {"id": "je-1", "amount": 500}
    assert adapter.post_journal_entry(sample_entry) is True
    assert adapter.posted_entries == [sample_entry]


def test_logistics_adapter_is_abstract():
    """LogisticsAdapter cannot be instantiated directly because generate_shipping_label is abstract."""
    with pytest.raises(TypeError) as excinfo:
        LogisticsAdapter()
    assert "Can't instantiate abstract class LogisticsAdapter" in str(excinfo.value)


def test_fedex_adapter_inheritance():
    """FedExAdapter must inherit from LogisticsAdapter."""
    assert issubclass(FedExAdapter, LogisticsAdapter)
    adapter = FedExAdapter()
    assert isinstance(adapter, LogisticsAdapter)


def test_fedex_adapter_generate_shipping_label_raises():
    """FedExAdapter.generate_shipping_label raises NotImplementedError with prescriptive message."""
    adapter = FedExAdapter()
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.generate_shipping_label("ord-12345")
    assert str(excinfo.value) == "Shipping label purchase is not implemented"
