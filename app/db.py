import os
from pathlib import Path

from sqlalchemy import inspect, text
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
    add_missing_columns()
    with Session(engine) as session:
        seed_defaults(session)
        seed_invoice_templates(session)


def add_missing_columns() -> None:
    """Migración mínima: agrega a las tablas existentes las columnas nuevas del modelo.

    `create_all` crea tablas nuevas pero no toca las existentes; así una base creada con
    una versión anterior sigue funcionando sin perder datos.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
                default = column.default.arg if column.default is not None and column.default.is_scalar else None
                if default is None and not column.nullable:
                    # SQLite no permite agregar columnas NOT NULL sin valor por defecto.
                    default = {"str": "", "int": 0, "float": 0, "bool": False}.get(
                        column.type.python_type.__name__)
                if isinstance(default, bool):
                    ddl += f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                conn.execute(text(ddl))


def seed_defaults(session: Session) -> None:
    """Crea los datos mínimos para arrancar: la persona 'Casa' y ubicaciones típicas."""
    from app.models import Location, Person

    if not session.exec(select(Person)).first():
        session.add(Person(name="Casa", kind="hogar", notes="Gastos comunes del hogar"))
    if not session.exec(select(Location)).first():
        for name in ("Alacena", "Heladera", "Freezer", "Baño", "Lavadero"):
            session.add(Location(name=name))
    session.commit()


def seed_invoice_templates(session: Session) -> None:
    from app.models import InvoiceTemplate
    from app.services.invoice_pdf import BUILTIN_PAYMENT_URLS, BUILTIN_TEMPLATES

    existing = {t.name: t for t in session.exec(
        select(InvoiceTemplate).where(InvoiceTemplate.builtin == True))}  # noqa: E712
    for name, keywords, category in BUILTIN_TEMPLATES:
        template = existing.get(name)
        if template is None:
            # Si el usuario borró o editó una incluida, no se la vuelve a crear con otro nombre.
            if existing:
                continue
            template = InvoiceTemplate(name=name, provider=name, keywords=keywords,
                                       category=category, builtin=True)
        if not template.payment_url and name in BUILTIN_PAYMENT_URLS:
            template.payment_url = BUILTIN_PAYMENT_URLS[name]
        session.add(template)
    session.commit()


def get_session():
    with Session(engine) as session:
        yield session
