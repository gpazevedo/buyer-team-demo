from fastapi import APIRouter, Depends

from test_tenant_app.auth.jwt import get_tenant_id
from test_tenant_app.clients.cost_client import cost_client
from test_tenant_app.models import CostPollResult

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.post("/poll", response_model=CostPollResult)
def poll_costs(tenant_id: str = Depends(get_tenant_id)):
    return cost_client.poll()
