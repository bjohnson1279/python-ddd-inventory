import pytest
from datetime import datetime, timezone
from src.domain.vision.entity import VisionInspection, InspectionStatus
from src.domain.vision.services import ComputerVisionService, QaGatewayService

def test_qa_gateway_passes_good_image():
    cv_service = ComputerVisionService()
    qa_service = QaGatewayService()
    
    inspection = VisionInspection(
        inspection_id="INS1",
        tenant_id="T1",
        dock_station_id="DOCK1",
        captured_at=datetime.now(timezone.utc),
        image_url="http://storage.com/image.jpg"
    )
    
    result = cv_service.analyze_image(inspection.image_url, inspection.inspection_id)
    qa_service.process_inspection(inspection, result)
    
    assert inspection.status == InspectionStatus.PASSED
    assert result.damage_score == 0.05
    assert len(result.anomalies_detected) == 0

def test_qa_gateway_flags_damaged_image():
    cv_service = ComputerVisionService()
    qa_service = QaGatewayService()
    
    inspection = VisionInspection(
        inspection_id="INS2",
        tenant_id="T1",
        dock_station_id="DOCK1",
        captured_at=datetime.now(timezone.utc),
        image_url="http://storage.com/damaged_box.jpg"
    )
    
    result = cv_service.analyze_image(inspection.image_url, inspection.inspection_id)
    qa_service.process_inspection(inspection, result, damage_threshold=0.5)
    
    assert inspection.status == InspectionStatus.FLAGGED
    assert result.damage_score == 0.85
    assert "CRUSHED_CORNER" in result.anomalies_detected
