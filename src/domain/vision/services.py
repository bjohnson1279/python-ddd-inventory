from .entity import VisionInspection, InspectionResult, VolumeDimensions, DimensionUnit

class ComputerVisionService:
    def analyze_image(self, image_url: str, inspection_id: str) -> InspectionResult:
        # In a real implementation, this would pass the image_url to an OpenCV/YOLO inference pipeline.
        # For the domain logic, we mock the inference output.
        
        # Simulate damage if "damaged" is in the url
        is_damaged = "damaged" in image_url.lower()
        damage_score = 0.85 if is_damaged else 0.05
        anomalies = ["CRUSHED_CORNER"] if is_damaged else []
        
        return InspectionResult(
            inspection_id=inspection_id,
            detected_barcode="123456789012",
            dimensions=VolumeDimensions(10.0, 10.0, 10.0, DimensionUnit.CM),
            damage_score=damage_score,
            anomalies_detected=anomalies
        )

class QaGatewayService:
    def process_inspection(self, inspection: VisionInspection, result: InspectionResult, damage_threshold: float = 0.70) -> None:
        if result.damage_score >= damage_threshold:
            inspection.flag_inspection()
        else:
            inspection.pass_inspection()
