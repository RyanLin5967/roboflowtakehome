"""Operator-only Roboflow review and delivery routes. No credentials in responses."""

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .destination import DestinationError
from .models import StrictModel
from .providers import ProviderError
from .roboflow_delivery import RoboflowDelivery, RoboflowDeliveryProvider


class DeliveryKey(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")


class AnnotationApproval(DeliveryKey):
    reason: str = Field(min_length=1, max_length=2000)


class CocoVerification(StrictModel):
    version: int = Field(ge=1, le=99999999)
    # Three exact parsed Roboflow _annotations.coco.json files, keyed by split.
    documents: dict = Field(max_length=3)


def mount_delivery(app, config, repo, auth):
    router = APIRouter(prefix="/api/operator", dependencies=[Depends(auth.require)])

    @contextmanager
    def service(*, remote=False):
        provider = None
        try:
            if remote:
                destination = app.state.destination.resolve(force=True)
                credentials = config.credentials()
                provider = RoboflowDeliveryProvider(
                    credentials["ROBOFLOW_API_KEY"].get_secret_value(),
                    destination["workspace"],
                    destination["project"],
                    timeout=15,
                )
            yield RoboflowDelivery(
                repo,
                provider or SimpleNamespace(workspace="", project=""),
                generation_limit=config.roboflow_generation_limit,
                budget_evidence=config.roboflow_generation_budget_evidence,
            )
        except (DestinationError, ProviderError) as error:
            raise HTTPException(
                409,
                detail={
                    "code": error.code,
                    "message": "Roboflow delivery requires a reachable private destination. "
                    "No uncertain external write is automatically repeated.",
                },
            ) from None
        finally:
            if provider is not None:
                provider.close()

    @router.get("/releases/{release_id}/roboflow")
    def release_delivery(release_id: str):
        with service() as delivery:
            return delivery.list_release(release_id)

    @router.post("/releases/{release_id}/roboflow/snapshots", status_code=201)
    def snapshot(release_id: str, body: DeliveryKey):
        with service(remote=True) as delivery:
            return delivery.sync_annotations(release_id, body.idempotency_key)

    @router.get("/roboflow/snapshots/{snapshot_id}")
    def get_snapshot(snapshot_id: str):
        with service() as delivery:
            return delivery.get_snapshot(snapshot_id)

    @router.post("/roboflow/snapshots/{snapshot_id}/approve")
    def approve(snapshot_id: str, body: AnnotationApproval):
        with service() as delivery:
            return delivery.approve(snapshot_id, body.idempotency_key, body.reason)

    @router.post("/roboflow/snapshots/{snapshot_id}/deliver", status_code=202)
    def request_delivery(snapshot_id: str, body: DeliveryKey):
        with service() as delivery:
            return delivery.request_delivery(snapshot_id, body.idempotency_key)

    @router.get("/roboflow/deliveries/{delivery_id}")
    def get_delivery(delivery_id: str):
        with service() as delivery:
            return delivery.get_delivery(delivery_id)

    @router.post("/roboflow/deliveries/{delivery_id}/advance")
    def advance(delivery_id: str):
        with service(remote=True) as delivery:
            return delivery.advance(delivery_id)

    @router.post("/roboflow/deliveries/{delivery_id}/verify-coco")
    def verify_coco(delivery_id: str, body: CocoVerification):
        with service() as delivery:
            return delivery.verify_coco(delivery_id, body.version, body.documents)

    app.include_router(router)
