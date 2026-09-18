import pytest
from src.domain.workflow import ApprovalWorkflowEngine, WorkflowExecution, ApprovalStep

def test_workflow_engine_register_template():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager", "finance"])
    assert "purchase_order" in engine._templates
    assert engine._templates["purchase_order"] == ["manager", "finance"]

def test_workflow_engine_start_workflow():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager", "finance"])

    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    assert execution is not None
    assert execution.trigger_event == "purchase_order"
    assert execution.payload == {"amount": 1000}
    assert len(execution.steps) == 2
    assert execution.steps[0].role == "manager"
    assert execution.steps[0].status == "PENDING"
    assert execution.steps[1].role == "finance"
    assert execution.steps[1].status == "PENDING"
    assert execution.status == "IN_PROGRESS"

def test_workflow_engine_start_workflow_unregistered():
    engine = ApprovalWorkflowEngine()
    execution = engine.start_workflow("unregistered_event", {"amount": 1000})
    assert execution is None

def test_workflow_execution_approve_step_success():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager", "finance"])
    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    # Approve first step
    success = execution.approve_step("manager", "user_1", "Looks good")
    assert success is True
    assert execution.steps[0].status == "APPROVED"
    assert execution.steps[0].approved_by == "user_1"
    assert execution.steps[0].comments == "Looks good"
    assert execution.steps[0].approved_at is not None
    assert execution.status == "IN_PROGRESS"

    # Approve second step
    success2 = execution.approve_step("finance", "user_2", "Approved funding")
    assert success2 is True
    assert execution.steps[1].status == "APPROVED"
    assert execution.steps[1].approved_by == "user_2"
    assert execution.steps[1].comments == "Approved funding"
    assert execution.steps[1].approved_at is not None
    assert execution.status == "APPROVED"

def test_workflow_execution_reject_step():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager", "finance"])
    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    # Reject first step
    success = execution.reject_step("manager", "user_1", "Too expensive")
    assert success is True
    assert execution.steps[0].status == "REJECTED"
    assert execution.steps[0].approved_by == "user_1"
    assert execution.steps[0].comments == "Too expensive"
    assert execution.steps[0].approved_at is not None
    assert execution.status == "REJECTED"

def test_workflow_execution_approve_invalid_role():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager"])
    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    # Try to approve a role that doesn't exist
    success = execution.approve_step("finance", "user_2", "Approved")
    assert success is False
    assert execution.status == "IN_PROGRESS"

    # Approve the existing role
    execution.approve_step("manager", "user_1")
    assert execution.status == "APPROVED"

    # Try to approve an already approved step
    success2 = execution.approve_step("manager", "user_3", "Double approve")
    assert success2 is False

def test_workflow_execution_reject_invalid_role():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager"])
    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    # Try to reject a role that doesn't exist
    success = execution.reject_step("finance", "user_2", "Rejected")
    assert success is False
    assert execution.status == "IN_PROGRESS"

    # Approve the existing role
    execution.approve_step("manager", "user_1")
    assert execution.status == "APPROVED"

    # Try to reject an already approved step
    success2 = execution.reject_step("manager", "user_3", "Reject after approve")
    assert success2 is False

def test_workflow_engine_overwrite_template():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager", "finance"])
    engine.register_template("purchase_order", ["director"])
    assert engine._templates["purchase_order"] == ["director"]

def test_workflow_engine_multiple_executions():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager"])

    exec1 = engine.start_workflow("purchase_order", {"id": 1})
    exec2 = engine.start_workflow("purchase_order", {"id": 2})

    assert exec1.execution_id != exec2.execution_id
    assert exec1.payload == {"id": 1}
    assert exec2.payload == {"id": 2}

def test_workflow_execution_approve_after_rejection():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager", "finance"])
    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    # Reject first step
    execution.reject_step("manager", "user_1", "Rejected")
    assert execution.status == "REJECTED"

    # Approve second step - shouldn't change overall status
    success = execution.approve_step("finance", "user_2", "Approved")
    assert success is True
    assert execution.steps[1].status == "APPROVED"
    assert execution.status == "REJECTED" # Overall status remains REJECTED

def test_workflow_engine_empty_roles():
    engine = ApprovalWorkflowEngine()
    engine.register_template("auto_approve", [])
    execution = engine.start_workflow("auto_approve", {})

    assert len(execution.steps) == 0
    # Evaluate status for empty steps should result in APPROVED
    execution._evaluate_overall_status()
    assert execution.status == "APPROVED"

def test_workflow_execution_timestamps():
    engine = ApprovalWorkflowEngine()
    engine.register_template("purchase_order", ["manager"])
    execution = engine.start_workflow("purchase_order", {"amount": 1000})

    assert execution.created_at is not None
    assert execution.steps[0].approved_at is None

    execution.approve_step("manager", "user_1")
    assert execution.steps[0].approved_at is not None
    assert execution.steps[0].approved_at >= execution.created_at
