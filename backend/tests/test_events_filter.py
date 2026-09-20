"""Tests for server-side event_type filtering on the timeline endpoint."""

import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
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
from app.database import Base
from app.main import app


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, compiler, **kw):  # pragma: no cover - test shim
    return "JSON"


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Point the app's module-level engine at SQLite so the FastAPI lifespan
    # create_all() and any imports do not require a running Postgres.
    import app.database as db_mod
    import app.main as main_mod

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(main_mod, "engine", engine)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    from app.database import get_db

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_current_user] = lambda: {
        "username": "researcher",
        "role": "researcher",
    }
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_mixed_run(db):
    """v1 RunStarted, v2/v3 MetricRecorded, v4 ArtifactAttached, v5 RunCompleted."""
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="n1",
        dataset_content_sha256=sha("ds"),
        code_commit_sha="abc1234",
        description="d",
        run_id=uuid4(),
    )
    run = record_metric(db, run_id=run.id, actor="researcher", name="acc",
                        value=0.9, step=1, expected_version=run.version)
    run = record_metric(db, run_id=run.id, actor="researcher", name="acc",
                        value=0.92, step=2, expected_version=run.version)
    run = attach_artifact(db, run_id=run.id, actor="researcher", name="m.bin",
                          uri="s3://b/m.bin", content_sha256=sha("m"),
                          media_type=None, expected_version=run.version)
    complete_run(db, run_id=run.id, actor="researcher",
                 result_summary="done", expected_version=run.version)
    return run


def test_list_events_filter_at_cqrs_level(db):
    run = _seed_mixed_run(db)
    metrics = list_events(db, run.id, ["MetricRecorded"])
    assert [e.event_type for e in metrics] == ["MetricRecorded", "MetricRecorded"]
    # 版本号必须与全量事件流里该类事件的版本号一致（不重新编号）
    assert [e.version for e in metrics] == [2, 3]


def test_events_endpoint_full_then_metric_only(client, db):
    run = _seed_mixed_run(db)

    full = client.get(f"/api/runs/{run.id}/events").json()
    assert [e["event_type"] for e in full] == [
        "RunStarted",
        "MetricRecorded",
        "MetricRecorded",
        "ArtifactAttached",
        "RunCompleted",
    ]

    # 只勾“记度量”：核对没有启动或完成事件混入，且版本号等于全量里的版本号
    filtered = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "MetricRecorded")],
    ).json()
    assert len(filtered) == 2
    assert all(e["event_type"] == "MetricRecorded" for e in filtered)
    assert {e["version"] for e in filtered} == {
        e["version"] for e in full if e["event_type"] == "MetricRecorded"
    }
    assert [e["version"] for e in filtered] == [2, 3]
    assert not any(e["event_type"] in {"RunStarted", "RunCompleted"} for e in filtered)


def test_events_endpoint_multi_select(client, db):
    run = _seed_mixed_run(db)
    res = client.get(
        f"/api/runs/{run.id}/events",
        params=[
            ("event_type", "RunStarted"),
            ("event_type", "RunCompleted"),
        ],
    )
    types = [e["event_type"] for e in res.json()]
    assert types == ["RunStarted", "RunCompleted"]


def test_events_endpoint_unknown_type_rejected(client, db):
    run = _seed_mixed_run(db)
    res = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "NotAnEvent")],
    )
    assert res.status_code == 400


def test_events_endpoint_filtered_empty_for_real_run(client, db):
    # 真实 run 但该类型无事件：返回空列表而非 404
    run = start_run(
        db, actor="researcher", project="p", name="n",
        dataset_content_sha256=sha("x"), code_commit_sha="abc1234",
        description=None, run_id=uuid4(),
    )
    res = client.get(
        f"/api/runs/{run.id}/events",
        params=[("event_type", "RunAborted")],
    )
    assert res.status_code == 200
    assert res.json() == []


def test_events_endpoint_missing_run_404(client):
    res = client.get(f"/api/runs/{uuid4()}/events")
    assert res.status_code == 404


def test_auditor_may_read_timeline(client, db):
    run = _seed_mixed_run(db)
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "auditor",
        "role": "auditor",
    }
    res = client.get(f"/api/runs/{run.id}/events")
    assert res.status_code == 200
    assert len(res.json()) == 5
