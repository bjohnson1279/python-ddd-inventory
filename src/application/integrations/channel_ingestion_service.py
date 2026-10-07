from typing import Any, List, Dict
import asyncio
import logging

logger = logging.getLogger(__name__)

class ChannelIngestionService:
    def __init__(self, inventory_repository: Any, dispatch_use_case: Any):
        self.inventory_repository = inventory_repository
        self.dispatch_use_case = dispatch_use_case

    async def ingest_order(self, channel_id: str, external_order_id: str, line_items: List[Dict[str, Any]], tenant_id: str) -> None:
        """
        Parses external orders, resolves oversell conflicts, and routes fulfillment.
        """
        logger.info(f"Ingesting order {external_order_id} from {channel_id} for tenant {tenant_id}")
        
        sku_quantities = {}
        for item in line_items:
            sku = item.get("sku")
            if sku:
                qty = item.get("quantity", 1)
                sku_quantities[sku] = sku_quantities.get(sku, 0) + qty

        execute_batch = getattr(self.dispatch_use_case, 'execute_batch', None)
        if callable(execute_batch):
            items = [{"sku": sku, "quantity": qty} for sku, qty in sku_quantities.items()]
            if items:
                await execute_batch(
                    items=items,
                    tenant_id=tenant_id,
                    skip_publish_to_channel=channel_id
                )
        else:
            tasks = [
                self.dispatch_use_case.execute(
                    sku=sku,
                    quantity=quantity,
                    tenant_id=tenant_id,
                    skip_publish_to_channel=channel_id
                )
                for sku, quantity in sku_quantities.items()
            ]
            if tasks:
                await asyncio.gather(*tasks)
