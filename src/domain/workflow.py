from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import uuid
import logging

logger = logging.getLogger(__name__)

@dataclass
class ApprovalStep:
    role: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED
    comments: str = ""

@dataclass
class WorkflowExecution:
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trigger_event: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    steps: List[ApprovalStep] = field(default_factory=list)
    status: str = "IN_PROGRESS"  # IN_PROGRESS, APPROVED, REJECTED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def approve_step(self, role: str, user_id: str, comments: str = ""):
        for step in self.steps:
            if step.role == role and step.status == "PENDING":
                step.status = "APPROVED"
                step.approved_by = user_id
                step.approved_at = datetime.now(timezone.utc)
                step.comments = comments
                self._evaluate_overall_status()
                return True
        return False

    def reject_step(self, role: str, user_id: str, comments: str = ""):
        for step in self.steps:
            if step.role == role and step.status == "PENDING":
                step.status = "REJECTED"
                step.approved_by = user_id
                step.approved_at = datetime.now(timezone.utc)
                step.comments = comments
                self.status = "REJECTED"
                return True
        return False

    def _evaluate_overall_status(self):
        if all(step.status == "APPROVED" for step in self.steps):
            self.status = "APPROVED"

class ApprovalWorkflowEngine:
    """Configurable Approval Workflows Engine."""
    
    def __init__(self):
        # Template registry: event_name -> list of required roles
        self._templates: Dict[str, List[str]] = {}
        
    def register_template(self, event_name: str, required_roles: List[str]):
        self._templates[event_name] = required_roles

    def start_workflow(self, event_name: str, payload: Dict[str, Any]) -> Optional[WorkflowExecution]:
        if event_name not in self._templates:
            return None
            
        steps = [ApprovalStep(role=role) for role in self._templates[event_name]]
        execution = WorkflowExecution(
            trigger_event=event_name,
            payload=payload,
            steps=steps
        )
        logger.info(f"Started approval workflow for {event_name}")
        return execution
