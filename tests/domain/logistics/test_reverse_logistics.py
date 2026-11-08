import pytest
from datetime import datetime, timezone
import uuid
from src.domain.logistics.reverse_logistics import RMACase, ReverseLogisticsWorkflow
from src.domain.logistics.value_objects import RMAStatus

def test_rma_case_instantiation():
    case = RMACase(order_id="ORD-123", customer_id="CUST-456")
    assert case.order_id == "ORD-123"
    assert case.customer_id == "CUST-456"
    assert case.status == "OPEN"
    assert case.status == RMAStatus.OPEN
    assert isinstance(case.status, RMAStatus)
    assert case.case_id is not None
    assert isinstance(case.case_id, str)
    assert case.created_at is not None
    assert isinstance(case.created_at, datetime)

def test_rma_case_status_coercion_and_validation():
    case = RMACase(order_id="ORD-1", customer_id="CUST-1", status=RMAStatus.INSPECTION)
    assert case.status == RMAStatus.INSPECTION

    case_str = RMACase(order_id="ORD-2", customer_id="CUST-2", status="SCRAPPED")
    assert case_str.status == RMAStatus.SCRAPPED
    assert case_str.status == "SCRAPPED"

    with pytest.raises(ValueError):
        RMACase(order_id="ORD-3", customer_id="CUST-3", status="INVALID_STATUS")

def test_reverse_logistics_workflow_create_rma():
    workflow = ReverseLogisticsWorkflow()
    case = workflow.create_rma(order_id="ORD-111", customer_id="CUST-222")

    assert len(workflow.rma_cases) == 1
    assert workflow.rma_cases[0] == case
    assert case.order_id == "ORD-111"
    assert case.customer_id == "CUST-222"
    assert case.status == RMAStatus.OPEN
    assert case.status == "OPEN"

def test_reverse_logistics_workflow_update_inspection_result_existing():
    workflow = ReverseLogisticsWorkflow()
    case = workflow.create_rma(order_id="ORD-999", customer_id="CUST-888")

    workflow.update_inspection_result(case.case_id, "REFUNDED")
    assert case.status == RMAStatus.REFUNDED
    assert case.status == "REFUNDED"

    workflow.update_inspection_result(case.case_id, RMAStatus.SCRAPPED)
    assert case.status == RMAStatus.SCRAPPED

    with pytest.raises(ValueError):
        workflow.update_inspection_result(case.case_id, "UNKNOWN_STATUS")

def test_reverse_logistics_workflow_update_inspection_result_non_existent():
    workflow = ReverseLogisticsWorkflow()
    case = workflow.create_rma(order_id="ORD-999", customer_id="CUST-888")

    # Update a non-existent case ID
    workflow.update_inspection_result("NON-EXISTENT-ID", "SCRAPPED")

    # Existing case should remain unchanged
    assert case.status == RMAStatus.OPEN
    assert len(workflow.rma_cases) == 1
