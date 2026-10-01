import pytest
import os
import json
import csv
from src.infrastructure.services.report_generator_service import ReportGeneratorService

@pytest.fixture
def service():
    return ReportGeneratorService()

@pytest.mark.asyncio
async def test_generate_report_csv(service):
    url = await service.generate_report("def-123", "exec-abc", "csv")
    
    assert url == "/uploads/reports/report_exec-abc.csv"
    assert os.path.exists("./uploads/reports/report_exec-abc.csv")
    
    with open("./uploads/reports/report_exec-abc.csv", "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    assert len(rows) == 2
    assert rows[0]["sku"] == "MOCK-1"
    
@pytest.mark.asyncio
async def test_generate_report_json(service):
    url = await service.generate_report("def-123", "exec-def", "json")
    
    assert url == "/uploads/reports/report_exec-def.json"
    assert os.path.exists("./uploads/reports/report_exec-def.json")
    
    with open("./uploads/reports/report_exec-def.json", "r") as f:
        data = json.load(f)
        
    assert len(data) == 2
    assert data[1]["sku"] == "MOCK-2"
