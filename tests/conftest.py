from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from cmr_leads.db.models import Base
from cmr_leads.db.session import get_or_create_workspace, make_engine, make_session_factory
from cmr_leads.pipeline.scoring import load_scoring_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as session:
        yield session


@pytest.fixture
def workspace(session):
    return get_or_create_workspace(session, "test")


@pytest.fixture
def scoring_config():
    return load_scoring_config(ROOT / "config" / "scoring.yaml")
