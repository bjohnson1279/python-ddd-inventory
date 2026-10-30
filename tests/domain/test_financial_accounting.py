import pytest
from src.domain.financial_accounting import (
    ACCOUNTS,
    checked_cents,
    validate_lines,
    pair,
    movement_facts,
)
from src.domain.product.layer_costing import MAX_SAFE_CENTS


def test_checked_cents_valid():
    assert checked_cents(0) == 0
    assert checked_cents(100) == 100
    assert checked_cents(MAX_SAFE_CENTS) == MAX_SAFE_CENTS
    assert checked_cents(-100, signed=True) == -100
    assert checked_cents(-MAX_SAFE_CENTS, signed=True) == -MAX_SAFE_CENTS


def test_checked_cents_invalid():
    with pytest.raises(ValueError, match="Amount must be exact integer cents"):
        checked_cents(10.5)
    with pytest.raises(ValueError, match="Amount must be exact integer cents"):
        checked_cents("100")
    with pytest.raises(ValueError, match="Amount must be exact integer cents"):
        checked_cents(None)
    with pytest.raises(ValueError, match="Amount must be exact integer cents"):
        checked_cents(-1)
    with pytest.raises(ValueError, match="Amount must be exact integer cents"):
        checked_cents(MAX_SAFE_CENTS + 1)
    with pytest.raises(ValueError, match="Amount must be exact integer cents"):
        checked_cents(-MAX_SAFE_CENTS - 1, signed=True)


def test_validate_lines_valid():
    lines = [
        {"accountCode": "1200", "type": "debit", "amountCents": 5000},
        {"accountCode": "2100", "type": "credit", "amountCents": 5000},
    ]
    assert validate_lines(lines) == 5000


def test_validate_lines_invalid_line_count():
    with pytest.raises(ValueError, match="A journal requires 2–100 lines"):
        validate_lines([{"accountCode": "1200", "type": "debit", "amountCents": 100}])
    with pytest.raises(ValueError, match="A journal requires 2–100 lines"):
        validate_lines([{"accountCode": "1200", "type": "debit", "amountCents": 100}] * 101)


def test_validate_lines_unknown_account_or_side():
    lines_bad_account = [
        {"accountCode": "9999", "type": "debit", "amountCents": 100},
        {"accountCode": "2100", "type": "credit", "amountCents": 100},
    ]
    with pytest.raises(ValueError, match="Unknown account code or journal side"):
        validate_lines(lines_bad_account)

    lines_bad_type = [
        {"accountCode": "1200", "type": "invalid", "amountCents": 100},
        {"accountCode": "2100", "type": "credit", "amountCents": 100},
    ]
    with pytest.raises(ValueError, match="Unknown account code or journal side"):
        validate_lines(lines_bad_type)


def test_validate_lines_zero_or_negative_amount():
    lines_zero = [
        {"accountCode": "1200", "type": "debit", "amountCents": 0},
        {"accountCode": "2100", "type": "credit", "amountCents": 0},
    ]
    with pytest.raises(ValueError, match="Journal lines must have positive amounts"):
        validate_lines(lines_zero)


def test_validate_lines_unbalanced():
    lines_unbalanced = [
        {"accountCode": "1200", "type": "debit", "amountCents": 5000},
        {"accountCode": "2100", "type": "credit", "amountCents": 4000},
    ]
    with pytest.raises(ValueError, match="Journal debits must equal credits"):
        validate_lines(lines_unbalanced)


def test_pair():
    res = pair("1200", "2100", 1000)
    assert res == [("1200", "debit", 1000), ("2100", "credit", 1000)]


def test_movement_facts_receive():
    res = movement_facts("receive", 10, {}, receipt_value=1000)
    assert res == {"kind": "RECEIPT", "postings": [("1200", "debit", 1000), ("2100", "credit", 1000)], "unknown": False}

    res_serial = movement_facts("serial-receive", 1, {"fromStatus": "pending"}, receipt_value=500)
    assert res_serial == {"kind": "RECEIPT", "postings": [("1200", "debit", 500), ("2100", "credit", 500)], "unknown": False}


def test_movement_facts_dispatch():
    res = movement_facts("dispatch", 2, {"costOfGoodsSoldCents": 1500})
    assert res == {"kind": "SALE", "postings": [("5000", "debit", 1500), ("1200", "credit", 1500)], "unknown": False}


def test_movement_facts_customer_return():
    res = movement_facts("customer-return", 1, {"cogsReversedCents": 1000, "writeOffCents": 200})
    assert res == {
        "kind": "CUSTOMER_RETURN",
        "postings": [("1200", "debit", 800), ("5100", "debit", 200), ("5000", "credit", 1000)],
        "unknown": False,
    }


def test_movement_facts_supplier_return():
    res = movement_facts("supplier-return", 1, {"supplierCreditExpectedCents": 1000, "inventoryValueChangeCents": -1200})
    assert res == {
        "kind": "SUPPLIER_RETURN",
        "postings": [("1300", "debit", 1000), ("1200", "credit", 1200), ("5200", "debit", 200)],
        "unknown": False,
    }

    res_credit_greater = movement_facts("supplier-return", 1, {"supplierCreditExpectedCents": 1200, "inventoryValueChangeCents": -1000})
    assert res_credit_greater == {
        "kind": "SUPPLIER_RETURN",
        "postings": [("1300", "debit", 1200), ("1200", "credit", 1000), ("5200", "credit", 200)],
        "unknown": False,
    }


def test_movement_facts_write_off():
    res = movement_facts("write-off", 1, {"inventoryValueChangeCents": -500})
    assert res == {"kind": "LOSS", "postings": [("5100", "debit", 500), ("1200", "credit", 500)], "unknown": False}

    res_zero = movement_facts("write-off", 0, {})
    assert res_zero == {"kind": "LOSS", "postings": [("5100", "debit", 0), ("1200", "credit", 0)], "unknown": False}


def test_movement_facts_adjust_and_price_layer():
    res_pos = movement_facts("adjust", 1, {"inventoryValueChangeCents": 300, "reasonCode": "COUNT"})
    assert res_pos == {"kind": "ADJUSTMENT", "postings": [("1200", "debit", 300), ("5200", "credit", 300)], "unknown": False}

    res_neg_damage = movement_facts("adjust", 1, {"inventoryValueChangeCents": -300, "reasonCode": "DAMAGE"})
    assert res_neg_damage == {"kind": "ADJUSTMENT", "postings": [("5100", "debit", 300), ("1200", "credit", 300)], "unknown": False}

    res_price = movement_facts("price-cost-layer", 1, {"unitCostCents": 100, "remainingQuantity": 5, "reasonCode": "AUDIT"})
    assert res_price == {"kind": "ADJUSTMENT", "postings": [("1200", "debit", 500), ("5200", "credit", 500)], "unknown": False}


def test_movement_facts_transfers():
    res_dispatch = movement_facts("transfer-dispatch", 1, {"transferredValueCents": 700})
    assert res_dispatch == {"kind": "TRANSIT", "postings": [("1210", "debit", 700), ("1200", "credit", 700)], "unknown": False}

    res_receive = movement_facts("transfer-receive", 1, {"inventoryValueChangeCents": 700})
    assert res_receive == {"kind": "TRANSIT", "postings": [("1200", "debit", 700), ("1210", "credit", 700)], "unknown": False}


def test_movement_facts_kits():
    res_comp = movement_facts("kit-assemble-component", 1, {"consumedLayers": [{"costCents": 100}, {"costCents": 200}]})
    assert res_comp == {"kind": "INTERNAL", "postings": [("1220", "debit", 300), ("1200", "credit", 300)], "unknown": False}

    res_assemble = movement_facts("kit-assemble", 1, {"carryingValueCents": 500})
    assert res_assemble == {"kind": "INTERNAL", "postings": [("1200", "debit", 500), ("1220", "credit", 500)], "unknown": False}

    res_disassemble = movement_facts("kit-disassemble", 1, {"carryingValueCents": 500})
    assert res_disassemble == {"kind": "INTERNAL", "postings": [("1220", "debit", 500), ("1200", "credit", 500)], "unknown": False}


def test_movement_facts_no_op_and_unknown():
    res_allocate = movement_facts("allocate", 1, {})
    assert res_allocate == {"kind": "INTERNAL", "postings": [], "unknown": False}

    res_unknown = movement_facts("non-existent-op", 1, {})
    assert res_unknown == {"kind": "REVIEW", "postings": [], "unknown": True}

    res_missing_vals = movement_facts("dispatch", 1, {})
    assert res_missing_vals == {"kind": "SALE", "postings": [("5000", "debit", None), ("1200", "credit", None)], "unknown": True}
