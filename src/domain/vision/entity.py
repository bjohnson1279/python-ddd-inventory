from dataclasses import dataclass, field
from enum import Enum
from typing import List
from datetime import datetime

class InspectionStatus(Enum):
    PENDING = "PENDING"
    ANALYZED = "ANALYZED"
    FLAGGED = "FLAGGED"
    PASSED = "PASSED"

class DimensionUnit(Enum):
    CM = "CM"
    INCH = "INCH"

@dataclass
class VolumeDimensions:
    length: float
    width: float
    height: float
    unit: DimensionUnit

@dataclass
class InspectionResult:
    inspection_id: str
    detected_barcode: str
    dimensions: VolumeDimensions
    damage_score: float
    anomalies_detected: List[str] = field(default_factory=list)

@dataclass
class VisionInspection:
    inspection_id: str
    tenant_id: str
    dock_station_id: str
    captured_at: datetime
    image_url: str
    status: InspectionStatus = InspectionStatus.PENDING
    
    def pass_inspection(self) -> None:
        if self.status not in (InspectionStatus.PENDING, InspectionStatus.ANALYZED):
            raise ValueError("Invalid status transition to PASSED")
        self.status = InspectionStatus.PASSED

    def flag_inspection(self) -> None:
        if self.status not in (InspectionStatus.PENDING, InspectionStatus.ANALYZED):
            raise ValueError("Invalid status transition to FLAGGED")
        self.status = InspectionStatus.FLAGGED
