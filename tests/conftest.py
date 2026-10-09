import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="house-erp-test-")
os.environ["HOUSE_ERP_DATA"] = _tmp
os.environ["HOUSE_ERP_DB"] = f"sqlite:///{_tmp}/test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def session():
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(monkeypatch):
    # Nunca salir a internet en los tests.
    monkeypatch.setattr("app.routers.stock.lookup_barcode", lambda code: None)
    monkeypatch.setattr("app.routers.general.lookup_barcode", lambda code: None)
    with TestClient(app) as c:
        yield c
