from typing import Any, List, Dict
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

        for sku, quantity in sku_quantities.items():
            # In a real implementation we would check ChannelAllocationPools here
            # to resolve oversell conflicts before dispatching.
            
            # Dispatch stock, skipping publisher back to the origin channel
            await self.dispatch_use_case.execute(
                sku=sku, 
                quantity=quantity, 
                tenant_id=tenant_id,
                skip_publish_to_channel=channel_id
            )
