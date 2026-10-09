import datetime as dt
from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel

UNITS = ["u", "kg", "g", "l", "ml", "paquete", "caja"]
PRODUCT_CATEGORIES = [
    "Almacén", "Bebidas", "Lácteos", "Carnes", "Verdulería", "Congelados",
    "Limpieza", "Higiene personal", "Farmacia", "Mascotas", "Bazar", "Otros",
]
MOVEMENT_KINDS = {"entrada": "Entrada", "salida": "Consumo", "ajuste": "Ajuste"}

INVOICE_CATEGORIES = [
    "Luz", "Gas", "Agua", "Internet", "Telefonía", "Cable/Streaming", "Expensas",
    "Alquiler", "Impuestos", "Seguros", "Salud", "Educación", "Supermercado",
    "Mantenimiento", "Otros",
]
CURRENCIES = ["ARS", "USD"]
PAYMENT_METHODS = ["Efectivo", "Transferencia", "Débito automático", "Tarjeta de débito",
                   "Tarjeta de crédito", "Mercado Pago", "Otro"]
FREQUENCIES = {"mensual": 1, "bimestral": 2, "trimestral": 3, "semestral": 6, "anual": 12}
PERSON_KINDS = {"hogar": "Hogar", "miembro": "Miembro de la casa", "tercero": "Tercero"}


# --------------------------------------------------------------------------- personas

class Person(SQLModel, table=True):
    """A quién pertenece una factura o quién la pagó (la casa, un integrante o un tercero)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    kind: str = "miembro"
    notes: str = ""


# --------------------------------------------------------------------------- stock

class Location(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    barcode: Optional[str] = Field(default=None, index=True, unique=True)
    name: str = Field(index=True)
    brand: str = ""
    category: str = "Otros"
    unit: str = "u"
    package_size: str = ""  # lo que informa el paquete, ej. "500 g"
    image_url: str = ""
    min_stock: float = 0
    location_id: Optional[int] = Field(default=None, foreign_key="location.id")
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    location: Optional[Location] = Relationship()
    movements: list["StockMovement"] = Relationship(
        back_populates="product", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

    @property
    def stock(self) -> float:
        return round(sum(m.quantity for m in self.movements), 3)

    @property
    def needs_restock(self) -> bool:
        return self.min_stock > 0 and self.stock < self.min_stock


class StockMovement(SQLModel, table=True):
    """Cada entrada/consumo/ajuste. El stock actual es la suma de las cantidades (con signo)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    kind: str = "entrada"
    quantity: float
    date: dt.date = Field(default_factory=dt.date.today)
    expires_on: Optional[dt.date] = None
    unit_price: Optional[float] = None
    notes: str = ""

    product: Optional[Product] = Relationship(back_populates="movements")


# --------------------------------------------------------------------------- facturas

class Service(SQLModel, table=True):
    """Un servicio o gasto recurrente (luz, internet, expensas, prepaga...)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    provider: str = ""
    category: str = "Otros"
    account_number: str = ""  # nro. de cliente / cuenta
    owner_id: Optional[int] = Field(default=None, foreign_key="person.id")
    frequency: str = "mensual"
    due_day: Optional[int] = None
    expected_amount: Optional[float] = None
    active: bool = True
    notes: str = ""

    owner: Optional[Person] = Relationship()
    invoices: list["Invoice"] = Relationship(back_populates="service")


class Invoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    provider_cuit: str = ""
    number: str = ""
    concept: str = ""
    category: str = "Otros"
    period: str = ""  # ej. "2026-09"
    owner_id: Optional[int] = Field(default=None, foreign_key="person.id")
    service_id: Optional[int] = Field(default=None, foreign_key="service.id")
    amount: float
    currency: str = "ARS"
    issue_date: date = Field(default_factory=date.today)
    due_date: Optional[date] = None
    account_number: str = ""  # nro. de cliente / cuenta que figura en la factura
    payment_url: str = ""  # link de pago online
    payment_code: str = ""  # código de pago electrónico (Link / Pago Mis Cuentas / Rapipago)
    attachment: str = ""  # nombre de archivo dentro de data/uploads
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    owner: Optional[Person] = Relationship()
    service: Optional[Service] = Relationship(back_populates="invoices")
    payments: list["Payment"] = Relationship(
        back_populates="invoice", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

    @property
    def paid(self) -> float:
        return round(sum(p.amount for p in self.payments), 2)

    @property
    def balance(self) -> float:
        return round(self.amount - self.paid, 2)

    def status_on(self, today: date) -> str:
        if self.balance <= 0:
            return "pagada"
        if self.due_date and self.due_date < today:
            return "vencida"
        if self.paid > 0:
            return "parcial"
        return "pendiente"

    @property
    def status(self) -> str:
        return self.status_on(date.today())


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoice.id", index=True)
    date: dt.date = Field(default_factory=dt.date.today)
    amount: float
    method: str = "Transferencia"
    paid_by_id: Optional[int] = Field(default=None, foreign_key="person.id")
    notes: str = ""

    invoice: Optional[Invoice] = Relationship(back_populates="payments")
    paid_by: Optional[Person] = Relationship()


class Settlement(SQLModel, table=True):
    """Cuando una persona le devuelve a otra lo que pagó por ella."""

    id: Optional[int] = Field(default=None, primary_key=True)
    from_id: int = Field(foreign_key="person.id")
    to_id: int = Field(foreign_key="person.id")
    amount: float
    currency: str = "ARS"
    date: dt.date = Field(default_factory=dt.date.today)
    notes: str = ""


class InvoiceTemplate(SQLModel, table=True):
    """Reglas para reconocer las facturas en PDF de un proveedor.

    Se identifica por CUIT o por palabras clave en el texto. Las expresiones regulares son
    opcionales: sólo hacen falta si la lectura genérica no encuentra bien algún dato.
    Cada expresión debe tener un grupo de captura con el valor.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    keywords: str = ""  # separadas por coma; alcanza con que aparezca una
    cuit: str = ""
    provider: str = ""
    category: str = "Otros"
    owner_id: Optional[int] = Field(default=None, foreign_key="person.id")
    service_id: Optional[int] = Field(default=None, foreign_key="service.id")
    amount_regex: str = ""
    due_regex: str = ""
    account_regex: str = ""
    builtin: bool = False
    notes: str = ""

    owner: Optional[Person] = Relationship()
    service: Optional[Service] = Relationship()

    @property
    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.keywords.split(",") if k.strip()]
