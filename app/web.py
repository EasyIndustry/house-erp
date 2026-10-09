"""Utilidades compartidas por los routers HTML."""
from datetime import date
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import models

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
STATIC_DIR = Path(__file__).parent / "static"


def money(value, currency: str = "ARS") -> str:
    if value is None:
        return ""
    symbol = "US$" if currency == "USD" else "$"
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{symbol} {formatted}"


def qty(value) -> str:
    if value is None:
        return ""
    return f"{value:g}".replace(".", ",")


def fdate(value) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


templates.env.filters["money"] = money
templates.env.filters["qty"] = qty
templates.env.filters["fdate"] = fdate
templates.env.globals.update(
    UNITS=models.UNITS,
    PRODUCT_CATEGORIES=models.PRODUCT_CATEGORIES,
    MOVEMENT_KINDS=models.MOVEMENT_KINDS,
    INVOICE_CATEGORIES=models.INVOICE_CATEGORIES,
    CURRENCIES=models.CURRENCIES,
    PAYMENT_METHODS=models.PAYMENT_METHODS,
    FREQUENCIES=models.FREQUENCIES,
    PERSON_KINDS=models.PERSON_KINDS,
)


def _section(path: str) -> str:
    if path == "/":
        return "home"
    if path.startswith("/stock/escanear"):
        return "scan"
    if path.startswith("/stock"):
        return "stock"
    if path.startswith("/facturas"):
        return "facturas"
    return "mas"


def render(request: Request, name: str, **context):
    context.setdefault("today", date.today())
    context.setdefault("section", _section(request.url.path))
    return templates.TemplateResponse(request, name, context)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# Los formularios HTML mandan "" para campos vacíos; estas funciones lo convierten a None.

def opt_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if "," in text:  # formato argentino: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def opt_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def opt_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
