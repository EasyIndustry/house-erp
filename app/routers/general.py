"""Tablero, personas/cuentas, ubicaciones, API JSON y exportaciones."""
import csv
import io
from datetime import date

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, select

from app.db import get_session
from app.web import STATIC_DIR
from app.models import Invoice, Location, Person, Product, Settlement, StockMovement
from app.services import reports
from app.services.afip_qr import InvalidAfipQR, parse_afip_qr
from app.services.barcode import lookup_barcode, normalize_barcode
from app.web import opt_date, opt_float, redirect, render

router = APIRouter()


@router.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    today = date.today()
    overdue, upcoming = reports.open_invoices(session, today)
    return render(
        request, "dashboard.html",
        overdue=overdue, upcoming=upcoming,
        shopping=reports.shopping_list(session),
        expiring=reports.expiring_soon(session, today),
        missing_services=reports.services_missing_invoice(session, today),
        spending=reports.monthly_spending(session, today.year, today.month),
        debts=reports.balances(session),
        product_count=len(session.exec(select(Product.id)).all()),
    )


@router.get("/mas")
def more_page(request: Request):
    return render(request, "more.html")


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    # Servido desde la raíz para que su alcance sea toda la app.
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


# --------------------------------------------------------------------------- personas

@router.get("/personas")
def people_page(request: Request, session: Session = Depends(get_session)):
    return render(
        request, "people.html",
        people=session.exec(select(Person).order_by(Person.kind, Person.name)).all(),
        locations=session.exec(select(Location).order_by(Location.name)).all(),
        debts=reports.balances(session),
        settlements=session.exec(select(Settlement).order_by(Settlement.date.desc())).all(),
    )


@router.post("/personas/guardar")
def person_save(person_id: str = Form(""), name: str = Form(...), kind: str = Form("miembro"),
                notes: str = Form(""), session: Session = Depends(get_session)):
    person = session.get(Person, int(person_id)) if person_id else Person(name=name)
    if person is None:
        raise HTTPException(404)
    person.name, person.kind, person.notes = name.strip(), kind, notes
    session.add(person)
    session.commit()
    return redirect("/personas")


@router.post("/personas/saldar")
def settle(from_id: int = Form(...), to_id: int = Form(...), amount: str = Form(...),
           currency: str = Form("ARS"), settle_date: str = Form(""), notes: str = Form(""),
           session: Session = Depends(get_session)):
    value = opt_float(amount)
    if not value or value <= 0 or from_id == to_id:
        raise HTTPException(400, "Datos de liquidación inválidos")
    session.add(Settlement(from_id=from_id, to_id=to_id, amount=value, currency=currency,
                           date=opt_date(settle_date) or date.today(), notes=notes))
    session.commit()
    return redirect("/personas")


@router.post("/ubicaciones/nueva")
def location_create(name: str = Form(...), session: Session = Depends(get_session)):
    name = name.strip()
    if name and not session.exec(select(Location).where(Location.name == name)).first():
        session.add(Location(name=name))
        session.commit()
    return redirect("/personas")


# --------------------------------------------------------------------------- API JSON

@router.get("/api/barcode/{code}")
def api_barcode(code: str, session: Session = Depends(get_session)):
    """Usado por el escáner: si el producto ya existe lo devuelve, si no consulta las APIs."""
    code = normalize_barcode(code)
    if not code:
        raise HTTPException(400, "Código inválido")
    product = session.exec(select(Product).where(Product.barcode == code)).first()
    if product:
        return {"exists": True, "product": {
            "id": product.id, "name": product.name, "brand": product.brand,
            "stock": product.stock, "unit": product.unit, "image_url": product.image_url,
        }}
    info = lookup_barcode(code)
    return {"exists": False, "found": info is not None, "info": info.to_dict() if info else None}


@router.post("/api/stock/{product_id:int}/ajustar")
def api_quick_move(product_id: int, payload: dict = Body(...), session: Session = Depends(get_session)):
    """Suma o resta unidades rápido desde el escáner. payload: {"delta": 1}."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(404)
    delta = float(payload.get("delta", 0))
    if delta:
        session.add(StockMovement(product_id=product_id, quantity=delta,
                                  kind="entrada" if delta > 0 else "salida", notes="Escáner"))
        session.commit()
        session.refresh(product)
    return {"id": product.id, "stock": product.stock}


@router.post("/api/afip-qr")
def api_afip_qr(payload: dict = Body(...), session: Session = Depends(get_session)):
    try:
        data = parse_afip_qr(str(payload.get("text", "")))
    except InvalidAfipQR as exc:
        raise HTTPException(400, str(exc))
    # Si ya cargamos facturas de ese CUIT, reutilizamos proveedor, categoría y dueño.
    previous = session.exec(
        select(Invoice).where(Invoice.provider_cuit == data["provider_cuit"])
        .order_by(Invoice.issue_date.desc())
    ).first()
    if previous:
        data.update(provider=previous.provider, category=previous.category,
                    owner_id=previous.owner_id, service_id=previous.service_id)
    return data


# --------------------------------------------------------------------------- exportar

def _csv_response(filename: str, header: list[str], rows) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/exportar/stock.csv")
def export_stock(session: Session = Depends(get_session)):
    products = session.exec(select(Product).order_by(Product.name)).all()
    return _csv_response(
        "stock.csv",
        ["codigo", "producto", "marca", "categoria", "ubicacion", "stock", "unidad", "minimo"],
        [[p.barcode or "", p.name, p.brand, p.category, p.location.name if p.location else "",
          p.stock, p.unit, p.min_stock] for p in products],
    )


@router.get("/exportar/facturas.csv")
def export_invoices(session: Session = Depends(get_session)):
    invoices = session.exec(select(Invoice).order_by(Invoice.issue_date)).all()
    return _csv_response(
        "facturas.csv",
        ["fecha", "vencimiento", "proveedor", "cuit", "numero", "concepto", "categoria",
         "periodo", "titular", "moneda", "importe", "pagado", "saldo", "estado"],
        [[i.issue_date, i.due_date or "", i.provider, i.provider_cuit, i.number, i.concept,
          i.category, i.period, i.owner.name if i.owner else "", i.currency, i.amount, i.paid,
          i.balance, i.status] for i in invoices],
    )
