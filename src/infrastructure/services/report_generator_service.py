import re
import os
import csv
import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class ReportGeneratorService:
    def __init__(self, db_session=None):
        self.db_session = db_session
        self.upload_dir = "/uploads/reports"
        # Mock file storage path
        os.makedirs(f".{self.upload_dir}", exist_ok=True)

    async def generate_report(self, report_definition_id: str, execution_id: str, format_str: str) -> str:
        # Prevent Path Traversal by strictly validating the format_str
        if not re.match(r'^[a-zA-Z0-9]+$', format_str):
            raise ValueError("Invalid format string")

        # Mock definition fetch
        # def = await db.fetch("SELECT * FROM report_definitions WHERE id = ?", report_definition_id)
        
        # Mock query logic based on type
        # For simplicity, we just return mock data
        data = [
            {"sku": "MOCK-1", "quantity": 100, "location": "default"},
            {"sku": "MOCK-2", "quantity": 50, "location": "default"}
        ]
        
        filename = f"report_{execution_id}.{format_str.lower()}"
        file_path = f".{self.upload_dir}/{filename}"
        public_url = f"{self.upload_dir}/{filename}"

        if format_str.lower() == "csv":
            return await self._generate_csv(file_path, public_url, data)
        elif format_str.lower() == "json":
            return await self._generate_json(file_path, public_url, data)
        else:
            # Fallback or stub for PDF/XLSX
            logger.warning(f"Format {format_str} not fully supported, falling back to JSON.")
            return await self._generate_json(file_path, public_url, data)

    async def _generate_csv(self, file_path: str, public_url: str, data: List[Dict[str, Any]]) -> str:
        if not data:
            with open(file_path, 'w', newline='') as f:
                pass
            return public_url

        with open(file_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        
        return public_url

    async def _generate_json(self, file_path: str, public_url: str, data: List[Dict[str, Any]]) -> str:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        return public_url
