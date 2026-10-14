from datetime import datetime
import pytest

from src.domain.vision.entity import (
    VisionInspection,
    InspectionResult,
    InspectionStatus,
    VolumeDimensions,
    DimensionUnit,
)
from src.domain.vision.services import ComputerVisionService, QaGatewayService


def test_analyze_image_damaged():
    service = ComputerVisionService()
    result = service.analyze_image(
        image_url="https://example.com/images/damaged_package.jpg",
        inspection_id="insp-001",
    )

    assert isinstance(result, InspectionResult)
    assert result.inspection_id == "insp-001"
    assert result.damage_score == 0.85
    assert result.anomalies_detected == ["CRUSHED_CORNER"]
    assert result.detected_barcode == "123456789012"
    assert result.dimensions == VolumeDimensions(10.0, 10.0, 10.0, DimensionUnit.CM)


def test_analyze_image_undamaged():
    service = ComputerVisionService()
    result = service.analyze_image(
        image_url="https://example.com/images/normal_package.jpg",
        inspection_id="insp-002",
    )

    assert isinstance(result, InspectionResult)
    assert result.inspection_id == "insp-002"
    assert result.damage_score == 0.05
    assert result.anomalies_detected == []


def test_analyze_image_case_insensitive_damaged():
    service = ComputerVisionService()
    result = service.analyze_image(
        image_url="https://example.com/images/DAMAGED_ITEM.PNG",
        inspection_id="insp-003",
    )

    assert result.damage_score == 0.85
    assert "CRUSHED_CORNER" in result.anomalies_detected


def test_qa_gateway_process_inspection_flagged():
    qa_service = QaGatewayService()
    inspection = VisionInspection(
        inspection_id="insp-001",
        tenant_id="tenant-1",
        dock_station_id="dock-1",
        captured_at=datetime.now(),
        image_url="https://example.com/images/damaged.jpg",
    )
    result = InspectionResult(
        inspection_id="insp-001",
        detected_barcode="123456789012",
        dimensions=VolumeDimensions(10.0, 10.0, 10.0, DimensionUnit.CM),
        damage_score=0.85,
        anomalies_detected=["CRUSHED_CORNER"],
    )

    qa_service.process_inspection(inspection, result)
    assert inspection.status == InspectionStatus.FLAGGED


def test_qa_gateway_process_inspection_passed():
    qa_service = QaGatewayService()
    inspection = VisionInspection(
        inspection_id="insp-002",
        tenant_id="tenant-1",
        dock_station_id="dock-1",
        captured_at=datetime.now(),
        image_url="https://example.com/images/clean.jpg",
    )
    result = InspectionResult(
        inspection_id="insp-002",
        detected_barcode="123456789012",
        dimensions=VolumeDimensions(10.0, 10.0, 10.0, DimensionUnit.CM),
        damage_score=0.05,
        anomalies_detected=[],
    )

    qa_service.process_inspection(inspection, result)
    assert inspection.status == InspectionStatus.PASSED


def test_qa_gateway_custom_threshold_and_exact_boundary():
    qa_service = QaGatewayService()

    # Boundary case: damage_score == damage_threshold (0.70) -> should flag (>= threshold)
    inspection_exact = VisionInspection(
        inspection_id="insp-003",
        tenant_id="tenant-1",
        dock_station_id="dock-1",
        captured_at=datetime.now(),
        image_url="https://example.com/images/boundary.jpg",
    )
    result_exact = InspectionResult(
        inspection_id="insp-003",
        detected_barcode="123456789012",
        dimensions=VolumeDimensions(10.0, 10.0, 10.0, DimensionUnit.CM),
        damage_score=0.70,
    )
    qa_service.process_inspection(inspection_exact, result_exact, damage_threshold=0.70)
    assert inspection_exact.status == InspectionStatus.FLAGGED

    # Custom higher threshold (0.90) with score 0.85 -> should pass
    inspection_custom = VisionInspection(
        inspection_id="insp-004",
        tenant_id="tenant-1",
        dock_station_id="dock-1",
        captured_at=datetime.now(),
        image_url="https://example.com/images/damaged.jpg",
    )
    result_custom = InspectionResult(
        inspection_id="insp-004",
        detected_barcode="123456789012",
        dimensions=VolumeDimensions(10.0, 10.0, 10.0, DimensionUnit.CM),
        damage_score=0.85,
    )
    qa_service.process_inspection(inspection_custom, result_custom, damage_threshold=0.90)
    assert inspection_custom.status == InspectionStatus.PASSED


def test_vision_inspection_status_transitions():
    inspection = VisionInspection(
        inspection_id="insp-005",
        tenant_id="tenant-1",
        dock_station_id="dock-1",
        captured_at=datetime.now(),
        image_url="https://example.com/images/test.jpg",
        status=InspectionStatus.ANALYZED,
    )

    # Test valid transition ANALYZED -> PASSED
    inspection.pass_inspection()
    assert inspection.status == InspectionStatus.PASSED

    # Test invalid transition PASSED -> FLAGGED
    with pytest.raises(ValueError, match="Invalid status transition to FLAGGED"):
        inspection.flag_inspection()

    # Test invalid transition PASSED -> PASSED
    with pytest.raises(ValueError, match="Invalid status transition to PASSED"):
        inspection.pass_inspection()
