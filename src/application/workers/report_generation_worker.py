import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

class ReportGenerationWorker:
    def __init__(self, db_session: Any, report_generator_service: Any):
        self.db = db_session
        self.generator = report_generator_service

    async def process_event(self, event_payload: str) -> None:
        try:
            payload = json.loads(event_payload)
            execution_id = payload.get("executionId")
            
            if not execution_id:
                raise ValueError("executionId missing from payload")

            # Mock DB fetch
            # execution = await self.db.fetch("SELECT * FROM report_executions WHERE id = ?", execution_id)
            # if not execution: raise Exception("Execution not found")
            
            execution = {
                "id": execution_id,
                "reportDefinitionId": "mock-def-123",
                "format": "csv"
            }

            try:
                # Update status to PROCESSING
                # await self.db.execute("UPDATE report_executions SET status = 'PROCESSING' WHERE id = ?", execution_id)

                file_url = await self.generator.generate_report(
                    report_definition_id=execution["reportDefinitionId"],
                    execution_id=execution["id"],
                    format_str=execution["format"]
                )

                # Update status to COMPLETED
                # await self.db.execute("UPDATE report_executions SET status = 'COMPLETED', file_url = ? WHERE id = ?", file_url, execution_id)
                logger.info(f"Report {execution_id} generated at {file_url}")
            except Exception as err:
                # Update status to FAILED
                logger.error(f"Failed to generate report {execution_id}: {err}")
                raise err

        except Exception as e:
            logger.error(f"Error processing report execution event: {e}")
