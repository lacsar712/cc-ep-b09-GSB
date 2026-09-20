import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.cqrs import (
    attach_artifact,
    complete_run,
    list_events,
    record_metric,
    start_run,
)
from app.database import Base, get_db
from app.main import app


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(_type, compiler, **kw):
        return "JSON"

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _five_event_run(db):
    """v1 RunStarted, v2/v3 MetricRecorded, v4 ArtifactAttached, v5 RunCompleted."""
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="filter-run",
        dataset_content_sha256=sha("ds-filter"),
        code_commit_sha="abc1234",
        description=None,
        run_id=uuid4(),
    )
    run = record_metric(
        db, run_id=run.id, actor="researcher", name="loss",
        value=0.9, step=1, expected_version=1,
    )
    run = record_metric(
        db, run_id=run.id, actor="researcher", name="loss",
        value=0.8, step=2, expected_version=2,
    )
    run = attach_artifact(
        db, run_id=run.id, actor="researcher", name="m.bin",
        uri="file:///m.bin", content_sha256=sha("m"),
        media_type="application/octet-stream", expected_version=3,
    )
    complete_run(
        db, run_id=run.id, actor="researcher",
        result_summary="ok", expected_version=4,
    )
    return run


def test_filter_only_metrics_keeps_true_versions(db):
    run = _five_event_run(db)

    metrics = list_events(db, run.id, ["MetricRecorded"])

    # Only MetricRecorded events: no RunStarted / RunCompleted mixed in.
    assert [e.event_type for e in metrics] == ["MetricRecorded", "MetricRecorded"]
    # Versions must equal these events' versions in the full stream (2 and 3),
    # not renumbered to 1/2.
    assert [e.version for e in metrics] == [2, 3]


def test_filter_multi_select_preserves_version_order(db):
    run = _five_event_run(db)

    picked = list_events(db, run.id, ("RunStarted", "MetricRecorded"))

    assert [e.event_type for e in picked] == [
        "RunStarted",
        "MetricRecorded",
        "MetricRecorded",
    ]
    assert [e.version for e in picked] == [1, 2, 3]
    assert all(e.event_type in {"RunStarted", "MetricRecorded"} for e in picked)


def test_no_filter_or_empty_returns_full_stream(db):
    run = _five_event_run(db)

    assert [e.version for e in list_events(db, run.id)] == [1, 2, 3, 4, 5]
    assert [e.version for e in list_events(db, run.id, [])] == [1, 2, 3, 4, 5]


@pytest.fixture()
def ctx(db):
    """TestClient backed by the same in-memory Session used to seed data,
    with a swappable authenticated role."""
    from contextlib import asynccontextmanager

    role = {"username": "researcher", "role": "researcher"}

    def _override_db():
        yield db

    def _override_user():
        return role

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    # Enter the portal properly but skip the real Postgres-backed lifespan.
    @asynccontextmanager
    async def _no_lifespan(_app):
        yield

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _no_lifespan
    try:
        with TestClient(app) as client:
            yield client, db, role
    finally:
        app.router.lifespan_context = original_lifespan
        app.dependency_overrides.clear()


def test_events_endpoint_filters_server_side(ctx):
    client, session, _role = ctx
    run = _five_event_run(session)

    full = client.get(f"/api/runs/{run.id}/events")
    assert full.status_code == 200
    assert [e["version"] for e in full.json()] == [1, 2, 3, 4, 5]

    # Repeated query params mirror the multi-select checkboxes.
    only_metrics = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "MetricRecorded")],
    )
    assert only_metrics.status_code == 200
    body = only_metrics.json()
    assert [e["event_type"] for e in body] == ["MetricRecorded", "MetricRecorded"]
    assert [e["version"] for e in body] == [2, 3]

    multi = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "RunStarted"), ("event_type", "RunCompleted")],
    )
    assert multi.status_code == 200
    assert [e["version"] for e in multi.json()] == [1, 5]


def test_events_endpoint_rejects_unknown_type(ctx):
    client, session, _role = ctx
    run = _five_event_run(session)

    resp = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "NotAnEvent")],
    )
    assert resp.status_code == 422


def test_auditor_can_read_timeline_and_filter(ctx):
    client, session, role = ctx
    role["role"] = "auditor"
    run = _five_event_run(session)

    resp = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "MetricRecorded")],
    )
    assert resp.status_code == 200
    assert {e["event_type"] for e in resp.json()} == {"MetricRecorded"}


def test_unknown_run_filtered_is_404(ctx):
    client, _session, _role = ctx

    resp = client.get(
        f"/api/runs/{uuid4()}/events",
        params=[("event_type", "MetricRecorded")],
    )
    assert resp.status_code == 404
