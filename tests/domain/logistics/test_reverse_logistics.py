import pytest
from datetime import datetime, timezone
import uuid
from src.domain.logistics.reverse_logistics import RMACase, ReverseLogisticsWorkflow

def test_rma_case_instantiation():
    case = RMACase(order_id="ORD-123", customer_id="CUST-456")
    assert case.order_id == "ORD-123"
    assert case.customer_id == "CUST-456"
    assert case.status == "OPEN"
    assert case.case_id is not None
    assert isinstance(case.case_id, str)
    assert case.created_at is not None
    assert isinstance(case.created_at, datetime)

def test_reverse_logistics_workflow_create_rma():
    workflow = ReverseLogisticsWorkflow()
    case = workflow.create_rma(order_id="ORD-111", customer_id="CUST-222")

    assert len(workflow.rma_cases) == 1
    assert workflow.rma_cases[0] == case
    assert case.order_id == "ORD-111"
    assert case.customer_id == "CUST-222"
    assert case.status == "OPEN"

def test_reverse_logistics_workflow_update_inspection_result_existing():
    workflow = ReverseLogisticsWorkflow()
    case = workflow.create_rma(order_id="ORD-999", customer_id="CUST-888")

    workflow.update_inspection_result(case.case_id, "REFUNDED")

    assert case.status == "REFUNDED"

def test_reverse_logistics_workflow_update_inspection_result_non_existent():
    workflow = ReverseLogisticsWorkflow()
    case = workflow.create_rma(order_id="ORD-999", customer_id="CUST-888")

    # Update a non-existent case ID
    workflow.update_inspection_result("NON-EXISTENT-ID", "SCRAPPED")

    # Existing case should remain unchanged
    assert case.status == "OPEN"
    assert len(workflow.rma_cases) == 1
