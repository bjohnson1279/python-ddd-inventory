from sqlalchemy import select
from src.infrastructure.return_models import QuarantineModel


async def record_quarantine_losses(session, tenant, consumptions):
    taken = {item['layerId']: item['quantity'] for item in consumptions}
    rows = await session.scalars(select(QuarantineModel).where(QuarantineModel.tenant_id == tenant,
                                                             QuarantineModel.status == 'QUARANTINED'))
    changed = []
    for row in rows:
        lost = sum(taken.get(layer_id, 0) for layer_id in row.layer_ids)
        if lost:
            row.quantity -= lost
            row.version += 1
            if row.quantity == 0:
                row.status = 'DISPOSED'
            changed.append({'id': row.id, 'quantity': row.quantity, 'status': row.status, 'version': row.version})
    return changed
