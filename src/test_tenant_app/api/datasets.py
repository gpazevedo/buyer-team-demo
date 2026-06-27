import os

from fastapi import APIRouter, Depends

from test_tenant_app.auth.jwt import get_tenant_id
from test_tenant_app.clients.dynamo_client import dynamo_client
from test_tenant_app.clients.master_data_client import master_data_client
from test_tenant_app.clients.skill_client import skill_client
from test_tenant_app.models import DatasetStatus, KraljicThresholds, LoadDatasetsRequest
from test_tenant_app.models.datasets import PhaseLoadStatus, PhaseStatus

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
ENV = os.getenv("ENV", "dev")


@router.get("/status", response_model=DatasetStatus)
def get_status(tenant_id: str = Depends(get_tenant_id)):
    raw = skill_client.get_dataset_status(tenant_id)
    thresholds_raw = master_data_client.get_thresholds(ENV)
    items_count = dynamo_client.count_items(tenant_id)

    phases = [
        PhaseStatus(
            phase=p["phase"],
            status=PhaseLoadStatus(p["status"]),
            entities_created=p.get("entities_created", 0),
            last_loaded_at=p.get("last_loaded_at"),
        )
        for p in raw.get("phases", [])
    ]

    return DatasetStatus(
        tenant_id=tenant_id,
        categories=raw.get("categories", 0),
        suppliers=raw.get("suppliers", 0),
        items=items_count,
        negotiations=raw.get("negotiations", 0),
        phases=phases,
        thresholds=KraljicThresholds(**thresholds_raw),
        last_loaded_at=raw.get("last_loaded_at"),
        all_phases_complete=raw.get("all_phases_complete", False),
    )


@router.post("/load")
def load_datasets(body: LoadDatasetsRequest, tenant_id: str = Depends(get_tenant_id)):
    return skill_client.load_datasets(tenant_id, body.datasets)


@router.post("/validate")
def validate_datasets(tenant_id: str = Depends(get_tenant_id)):
    return skill_client.validate_datasets(tenant_id)


@router.post("/reset")
def reset_datasets(tenant_id: str = Depends(get_tenant_id)):
    return skill_client.reset_tenant_data(tenant_id)
