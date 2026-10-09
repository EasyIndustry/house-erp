import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HOUSE_ERP_DATA", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE_URL = os.environ.get("HOUSE_ERP_DB", f"sqlite:///{DATA_DIR / 'house.db'}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    from app import models

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_defaults(session)


def seed_defaults(session: Session) -> None:
    """Crea los datos mínimos para arrancar: la persona 'Casa' y ubicaciones típicas."""
    from app.models import Location, Person

    if not session.exec(select(Person)).first():
        session.add(Person(name="Casa", kind="hogar", notes="Gastos comunes del hogar"))
    if not session.exec(select(Location)).first():
        for name in ("Alacena", "Heladera", "Freezer", "Baño", "Lavadero"):
            session.add(Location(name=name))
    session.commit()


def get_session():
    with Session(engine) as session:
        yield session
