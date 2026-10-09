import shutil
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, or_, select

from app.db import UPLOAD_DIR, get_session
from app.models import Invoice, Payment, Person, Service
from app.web import opt_date, opt_float, opt_int, redirect, render

router = APIRouter()

ALLOWED_ATTACHMENTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
STATUSES = ["pendiente", "parcial", "vencida", "pagada"]


def _people(session: Session) -> list[Person]:
    return session.exec(select(Person).order_by(Person.kind, Person.name)).all()


def _get_invoice(session: Session, invoice_id: int) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Factura no encontrada")
    return invoice


def _save_attachment(upload: UploadFile | None) -> str:
    if not upload or not upload.filename:
        return ""
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENTS:
        raise HTTPException(400, "Formato de adjunto no permitido (PDF o imagen)")
    filename = f"{uuid.uuid4().hex}{suffix}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with (UPLOAD_DIR / filename).open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return filename


# --------------------------------------------------------------------------- facturas

@router.get("/facturas")
def invoice_list(request: Request, q: str = "", status: str = "", owner: str = "",
                 category: str = "", period: str = "", session: Session = Depends(get_session)):
    stmt = select(Invoice).order_by(Invoice.due_date.desc(), Invoice.issue_date.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Invoice.provider.ilike(like), Invoice.concept.ilike(like),
                              Invoice.number.ilike(like)))
    if owner:
        stmt = stmt.where(Invoice.owner_id == int(owner))
    if category:
        stmt = stmt.where(Invoice.category == category)
    if period:
        stmt = stmt.where(Invoice.period == period)
    invoices = session.exec(stmt).all()
    if status:
        invoices = [i for i in invoices if i.status == status]
    totals: dict[str, dict[str, float]] = {}
    for inv in invoices:
        t = totals.setdefault(inv.currency, {"amount": 0, "paid": 0, "balance": 0})
        t["amount"] += inv.amount
        t["paid"] += inv.paid
        t["balance"] += max(inv.balance, 0)
    return render(request, "invoices/list.html", invoices=invoices, totals=totals, q=q,
                  status=status, owner=owner, category=category, period=period,
                  statuses=STATUSES, people=_people(session))


@router.get("/facturas/nueva")
def invoice_new(request: Request, service_id: str = "", session: Session = Depends(get_session)):
    invoice = Invoice(provider="", amount=0, period=date.today().strftime("%Y-%m"))
    service = session.get(Service, int(service_id)) if service_id else None
    if service:
        invoice.service_id = service.id
        invoice.provider = service.provider or service.name
        invoice.concept = service.name
        invoice.category = service.category
        invoice.owner_id = service.owner_id
        invoice.amount = service.expected_amount or 0
        if service.due_day:
            today = date.today()
            invoice.due_date = date(today.year, today.month, min(service.due_day, 28))
    else:
        casa = session.exec(select(Person).where(Person.kind == "hogar")).first()
        invoice.owner_id = casa.id if casa else None
    return render(request, "invoices/form.html", invoice=invoice, people=_people(session),
                  services=session.exec(select(Service).where(Service.active == True)).all())  # noqa: E712


def _apply_invoice_form(invoice: Invoice, form: dict) -> None:
    invoice.provider = form["provider"].strip()
    invoice.provider_cuit = form["provider_cuit"].strip()
    invoice.number = form["number"].strip()
    invoice.concept = form["concept"].strip()
    invoice.category = form["category"]
    invoice.period = form["period"].strip()
    invoice.owner_id = opt_int(form["owner_id"])
    invoice.service_id = opt_int(form["service_id"])
    invoice.amount = opt_float(form["amount"]) or 0
    invoice.currency = form["currency"]
    invoice.issue_date = opt_date(form["issue_date"]) or date.today()
    invoice.due_date = opt_date(form["due_date"])
    invoice.notes = form["notes"]


@router.post("/facturas/nueva")
async def invoice_create(request: Request, attachment: UploadFile | None = File(None),
                         session: Session = Depends(get_session)):
    form = await request.form()
    invoice = Invoice(provider="", amount=0)
    _apply_invoice_form(invoice, form)
    invoice.attachment = _save_attachment(attachment)
    session.add(invoice)
    session.flush()
    if form.get("paid_now"):
        session.add(Payment(invoice_id=invoice.id, amount=invoice.amount,
                            date=invoice.issue_date, method=form.get("method") or "Transferencia",
                            paid_by_id=opt_int(form.get("paid_by_id"))))
    session.commit()
    return redirect(f"/facturas/{invoice.id}")


@router.get("/facturas/{invoice_id}")
def invoice_detail(request: Request, invoice_id: int, session: Session = Depends(get_session)):
    return render(request, "invoices/detail.html", invoice=_get_invoice(session, invoice_id),
                  people=_people(session))


@router.get("/facturas/{invoice_id}/editar")
def invoice_edit(request: Request, invoice_id: int, session: Session = Depends(get_session)):
    return render(request, "invoices/form.html", invoice=_get_invoice(session, invoice_id),
                  people=_people(session), services=session.exec(select(Service)).all())


@router.post("/facturas/{invoice_id}/editar")
async def invoice_update(request: Request, invoice_id: int,
                         attachment: UploadFile | None = File(None),
                         session: Session = Depends(get_session)):
    invoice = _get_invoice(session, invoice_id)
    form = await request.form()
    _apply_invoice_form(invoice, form)
    new_file = _save_attachment(attachment)
    if new_file:
        invoice.attachment = new_file
    session.add(invoice)
    session.commit()
    return redirect(f"/facturas/{invoice_id}")


@router.post("/facturas/{invoice_id}/eliminar")
def invoice_delete(invoice_id: int, session: Session = Depends(get_session)):
    session.delete(_get_invoice(session, invoice_id))
    session.commit()
    return redirect("/facturas")


@router.get("/facturas/{invoice_id}/adjunto")
def invoice_attachment(invoice_id: int, session: Session = Depends(get_session)):
    invoice = _get_invoice(session, invoice_id)
    path = UPLOAD_DIR / invoice.attachment
    if not invoice.attachment or not path.is_file():
        raise HTTPException(404, "La factura no tiene adjunto")
    return FileResponse(path)


@router.post("/facturas/{invoice_id}/pagar")
def invoice_pay(invoice_id: int, amount: str = Form(...), payment_date: str = Form(""),
                method: str = Form("Transferencia"), paid_by_id: str = Form(""),
                notes: str = Form(""), session: Session = Depends(get_session)):
    _get_invoice(session, invoice_id)
    value = opt_float(amount)
    if not value or value <= 0:
        raise HTTPException(400, "El monto del pago debe ser mayor a cero")
    session.add(Payment(invoice_id=invoice_id, amount=value,
                        date=opt_date(payment_date) or date.today(), method=method,
                        paid_by_id=opt_int(paid_by_id), notes=notes))
    session.commit()
    return redirect(f"/facturas/{invoice_id}")


@router.post("/pagos/{payment_id}/eliminar")
def payment_delete(payment_id: int, session: Session = Depends(get_session)):
    payment = session.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404)
    invoice_id = payment.invoice_id
    session.delete(payment)
    session.commit()
    return redirect(f"/facturas/{invoice_id}")


# --------------------------------------------------------------------------- servicios

@router.get("/servicios")
def service_list(request: Request, session: Session = Depends(get_session)):
    services = session.exec(select(Service).order_by(Service.active.desc(), Service.name)).all()
    return render(request, "invoices/services.html", services=services, people=_people(session))


@router.get("/servicios/nuevo")
def service_new(request: Request, session: Session = Depends(get_session)):
    return render(request, "invoices/service_form.html", service=Service(name=""),
                  people=_people(session))


@router.get("/servicios/{service_id}/editar")
def service_edit(request: Request, service_id: int, session: Session = Depends(get_session)):
    service = session.get(Service, service_id)
    if not service:
        raise HTTPException(404)
    return render(request, "invoices/service_form.html", service=service, people=_people(session))


@router.post("/servicios/guardar")
def service_save(
    service_id: str = Form(""), name: str = Form(...), provider: str = Form(""),
    category: str = Form("Otros"), account_number: str = Form(""), owner_id: str = Form(""),
    frequency: str = Form("mensual"), due_day: str = Form(""), expected_amount: str = Form(""),
    active: str = Form(""), notes: str = Form(""), session: Session = Depends(get_session),
):
    service = session.get(Service, int(service_id)) if service_id else Service(name=name)
    if service is None:
        raise HTTPException(404)
    service.name = name.strip()
    service.provider = provider.strip()
    service.category = category
    service.account_number = account_number.strip()
    service.owner_id = opt_int(owner_id)
    service.frequency = frequency
    service.due_day = opt_int(due_day)
    service.expected_amount = opt_float(expected_amount)
    service.active = bool(active)
    service.notes = notes
    session.add(service)
    session.commit()
    return redirect("/servicios")
