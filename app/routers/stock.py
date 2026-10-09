from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, or_, select

from app.db import get_session
from app.models import Location, Product, StockMovement
from app.services.barcode import lookup_barcode, normalize_barcode
from app.services.reports import shopping_list
from app.web import opt_date, opt_float, opt_int, redirect, render

router = APIRouter()


def _get_product(session: Session, product_id: int) -> Product:
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    return product


@router.get("/stock")
def product_list(request: Request, q: str = "", category: str = "", location: str = "",
                 low: bool = False, session: Session = Depends(get_session)):
    stmt = select(Product).order_by(Product.name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.brand.ilike(like),
                              Product.barcode.ilike(like)))
    if category:
        stmt = stmt.where(Product.category == category)
    if location:
        stmt = stmt.where(Product.location_id == int(location))
    products = session.exec(stmt).all()
    if low:
        products = [p for p in products if p.needs_restock]
    return render(request, "stock/list.html", products=products, q=q, category=category,
                  location=location, low=low,
                  locations=session.exec(select(Location).order_by(Location.name)).all())


@router.get("/stock/escanear")
def scan_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "stock/scan.html")


@router.get("/stock/compras")
def shopping(request: Request, session: Session = Depends(get_session)):
    return render(request, "stock/shopping.html", items=shopping_list(session))


@router.get("/stock/nuevo")
def product_new(request: Request, barcode: str = "", session: Session = Depends(get_session)):
    barcode = normalize_barcode(barcode)
    if barcode:
        existing = session.exec(select(Product).where(Product.barcode == barcode)).first()
        if existing:
            return redirect(f"/stock/{existing.id}")
    info = lookup_barcode(barcode) if barcode else None
    product = Product(
        barcode=barcode or None,
        name=info.name if info else "",
        brand=info.brand if info else "",
        category=(info.category if info and info.category else "Otros"),
        package_size=info.package_size if info else "",
        image_url=info.image_url if info else "",
    )
    return render(request, "stock/form.html", product=product, info=info,
                  looked_up=bool(barcode), locations=session.exec(select(Location)).all())


def _apply_form(product: Product, barcode, name, brand, category, unit, package_size,
                image_url, min_stock, location_id, notes) -> None:
    product.barcode = normalize_barcode(barcode) or None
    product.name = name.strip()
    product.brand = brand.strip()
    product.category = category
    product.unit = unit
    product.package_size = package_size.strip()
    product.image_url = image_url.strip()
    product.min_stock = opt_float(min_stock) or 0
    product.location_id = opt_int(location_id)
    product.notes = notes


@router.post("/stock/nuevo")
def product_create(
    barcode: str = Form(""), name: str = Form(...), brand: str = Form(""),
    category: str = Form("Otros"), unit: str = Form("u"), package_size: str = Form(""),
    image_url: str = Form(""), min_stock: str = Form(""), location_id: str = Form(""),
    notes: str = Form(""), initial_qty: str = Form(""), expires_on: str = Form(""),
    unit_price: str = Form(""), session: Session = Depends(get_session),
):
    product = Product(name=name)
    _apply_form(product, barcode, name, brand, category, unit, package_size, image_url,
                min_stock, location_id, notes)
    if product.barcode and session.exec(select(Product).where(Product.barcode == product.barcode)).first():
        raise HTTPException(400, "Ya existe un producto con ese código de barras")
    session.add(product)
    session.flush()
    initial = opt_float(initial_qty)
    if initial:
        session.add(StockMovement(product_id=product.id, kind="entrada", quantity=initial,
                                  expires_on=opt_date(expires_on), unit_price=opt_float(unit_price),
                                  notes="Stock inicial"))
    session.commit()
    return redirect(f"/stock/{product.id}")


@router.get("/stock/{product_id:int}")
def product_detail(request: Request, product_id: int, session: Session = Depends(get_session)):
    product = _get_product(session, product_id)
    movements = sorted(product.movements, key=lambda m: (m.date, m.id), reverse=True)
    return render(request, "stock/detail.html", product=product, movements=movements)


@router.get("/stock/{product_id:int}/editar")
def product_edit(request: Request, product_id: int, session: Session = Depends(get_session)):
    return render(request, "stock/form.html", product=_get_product(session, product_id),
                  info=None, looked_up=False, locations=session.exec(select(Location)).all())


@router.post("/stock/{product_id:int}/editar")
def product_update(
    product_id: int, barcode: str = Form(""), name: str = Form(...), brand: str = Form(""),
    category: str = Form("Otros"), unit: str = Form("u"), package_size: str = Form(""),
    image_url: str = Form(""), min_stock: str = Form(""), location_id: str = Form(""),
    notes: str = Form(""), session: Session = Depends(get_session),
):
    product = _get_product(session, product_id)
    _apply_form(product, barcode, name, brand, category, unit, package_size, image_url,
                min_stock, location_id, notes)
    clash = product.barcode and session.exec(
        select(Product).where(Product.barcode == product.barcode, Product.id != product_id)).first()
    if clash:
        raise HTTPException(400, "Ya existe otro producto con ese código de barras")
    session.add(product)
    session.commit()
    return redirect(f"/stock/{product_id}")


@router.post("/stock/{product_id:int}/eliminar")
def product_delete(product_id: int, session: Session = Depends(get_session)):
    session.delete(_get_product(session, product_id))
    session.commit()
    return redirect("/stock")


@router.post("/stock/{product_id:int}/movimiento")
def add_movement(
    product_id: int, kind: str = Form(...), quantity: str = Form(...),
    movement_date: str = Form(""), expires_on: str = Form(""), unit_price: str = Form(""),
    notes: str = Form(""), session: Session = Depends(get_session),
):
    product = _get_product(session, product_id)
    value = opt_float(quantity) or 0
    if kind == "salida":
        delta = -abs(value)
    elif kind == "ajuste":  # la cantidad indica el stock real contado
        delta = value - product.stock
    else:
        delta = abs(value)
    if delta == 0:
        return redirect(f"/stock/{product_id}")
    session.add(StockMovement(
        product_id=product_id, kind=kind, quantity=delta,
        date=opt_date(movement_date) or date.today(),
        expires_on=opt_date(expires_on) if kind == "entrada" else None,
        unit_price=opt_float(unit_price) if kind == "entrada" else None, notes=notes,
    ))
    session.commit()
    return redirect(f"/stock/{product_id}")


@router.post("/stock/movimiento/{movement_id:int}/eliminar")
def delete_movement(movement_id: int, session: Session = Depends(get_session)):
    movement = session.get(StockMovement, movement_id)
    if not movement:
        raise HTTPException(404)
    product_id = movement.product_id
    session.delete(movement)
    session.commit()
    return redirect(f"/stock/{product_id}")
