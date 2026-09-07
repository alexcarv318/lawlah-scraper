import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Event, Lock, Thread
from time import monotonic

from fastapi import FastAPI, HTTPException

from src.classification.repository import LocalClassifier, resolve_device
from src.classification.schema import (
    ClassifyItem,
    ClassifyRequest,
    ClassifyResponse,
    HealthResponse,
)
from src.logger import get_logger

logger = get_logger(__name__)

IDLE_SECONDS = int(os.environ.get("CLASSIFY_IDLE_SECONDS", "300"))
CHECK_EVERY_SECONDS = 30

classifiers: dict[str, LocalClassifier] = {}
last_request_at = monotonic()
activity_lock = Lock()
stop_idle = Event()


def mark_request() -> None:
    global last_request_at
    with activity_lock:
        last_request_at = monotonic()


def idle_seconds() -> float:
    with activity_lock:
        return monotonic() - last_request_at


def stop_this_instance() -> None:
    from urllib.request import Request, urlopen

    token_request = Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    with urlopen(token_request, timeout=2) as token_response:
        token = token_response.read().decode()
    identity_request = Request(
        "http://169.254.169.254/latest/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urlopen(identity_request, timeout=2) as identity_response:
        instance_id = identity_response.read().decode()
    region_request = Request(
        "http://169.254.169.254/latest/meta-data/placement/region",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urlopen(region_request, timeout=2) as region_response:
        region = region_response.read().decode()

    import boto3

    logger.info("Classifier idle for %s seconds; stopping %s", int(idle_seconds()), instance_id)
    boto3.client("ec2", region_name=region).stop_instances(InstanceIds=[instance_id])


def watch_idle() -> None:
    while not stop_idle.wait(CHECK_EVERY_SECONDS):
        if classifiers and idle_seconds() >= IDLE_SECONDS:
            stop_this_instance()
            return


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    for source in ("judgments", "legislation"):
        classifiers[source] = LocalClassifier.load(source)
    mark_request()
    watcher = Thread(target=watch_idle, name="classifier-idle", daemon=True)
    watcher.start()
    logger.info("Classifier API ready on %s", resolve_device())
    yield
    stop_idle.set()


app = FastAPI(title="Lawlah classifier", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        ready=len(classifiers) == 2,
        device=str(resolve_device()),
    )


@app.post("/classify", response_model=ClassifyResponse)
def classify(request: ClassifyRequest) -> ClassifyResponse:
    mark_request()
    classifier = classifiers.get(request.source)
    if classifier is None:
        raise HTTPException(status_code=503, detail=f"{request.source} classifier is not loaded")

    previous = request.previous_texts or None
    results = classifier.classify_many(request.texts, previous)
    return ClassifyResponse(
        items=[
            ClassifyItem(role=item.role, topics=list(item.topics), concepts=list(item.concepts))
            for item in results
        ]
    )
