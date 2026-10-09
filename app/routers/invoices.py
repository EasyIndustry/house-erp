import re
import shutil
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, or_, select

from app.db import UPLOAD_DIR, get_session
from app.models import Invoice, InvoiceTemplate, Payment, Person, Service
from app.services.invoice_pdf import ParsedInvoice, extract_pdf, parse_invoice
from app.web import opt_date, opt_float, opt_int, redirect, render

router = APIRouter()

ALLOWED_ATTACHMENTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_PDF_BYTES = 15 * 1024 * 1024
STORED_PDF_RE = re.compile(r"[0-9a-f]{32}\.pdf")
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


def _apply_invoice_form(invoice: Invoice, form) -> None:
    get = lambda key: (form.get(key) or "").strip()  # noqa: E731
    invoice.provider = get("provider")
    invoice.provider_cuit = re.sub(r"\D", "", get("provider_cuit"))
    invoice.number = get("number")
    invoice.concept = get("concept")
    invoice.category = get("category") or "Otros"
    invoice.period = get("period")
    invoice.owner_id = opt_int(get("owner_id"))
    invoice.service_id = opt_int(get("service_id"))
    invoice.amount = opt_float(get("amount")) or 0
    invoice.currency = get("currency") or "ARS"
    invoice.issue_date = opt_date(get("issue_date")) or date.today()
    invoice.due_date = opt_date(get("due_date"))
    invoice.account_number = get("account_number")
    invoice.payment_url = get("payment_url")
    invoice.payment_code = get("payment_code")
    invoice.notes = form.get("notes") or ""


def _stored_pdf(name: str) -> str:
    """Valida el nombre de un PDF ya subido por la importación (viene en un campo oculto)."""
    name = (name or "").strip()
    if STORED_PDF_RE.fullmatch(name) and (UPLOAD_DIR / name).is_file():
        return name
    return ""


def _remember_template(session: Session, invoice: Invoice) -> None:
    """Crea (o actualiza) una plantilla para que la próxima factura de este proveedor se
    reconozca sola con el mismo titular, categoría y servicio."""
    if not invoice.provider:
        return
    template = None
    if invoice.provider_cuit:
        template = session.exec(select(InvoiceTemplate).where(
            InvoiceTemplate.cuit == invoice.provider_cuit, InvoiceTemplate.builtin == False)).first()  # noqa: E712
    if template is None:
        template = session.exec(select(InvoiceTemplate).where(
            InvoiceTemplate.name == invoice.provider, InvoiceTemplate.builtin == False)).first()  # noqa: E712
    if template is None:
        template = InvoiceTemplate(name=invoice.provider, cuit=invoice.provider_cuit,
                                   keywords="" if invoice.provider_cuit else invoice.provider)
    template.provider = invoice.provider
    template.category = invoice.category
    template.owner_id = invoice.owner_id
    template.service_id = invoice.service_id
    session.add(template)


@router.post("/facturas/nueva")
async def invoice_create(request: Request, attachment: UploadFile | None = File(None),
                         session: Session = Depends(get_session)):
    form = await request.form()
    invoice = Invoice(provider="", amount=0)
    _apply_invoice_form(invoice, form)
    invoice.attachment = _save_attachment(attachment) or _stored_pdf(form.get("attachment_name"))
    session.add(invoice)
    session.flush()
    if form.get("remember_template"):
        _remember_template(session, invoice)
    if form.get("paid_now"):
        session.add(Payment(invoice_id=invoice.id, amount=invoice.amount,
                            date=invoice.issue_date, method=form.get("method") or "Transferencia",
                            paid_by_id=opt_int(form.get("paid_by_id"))))
    session.commit()
    return redirect(f"/facturas/{invoice.id}")


@router.get("/facturas/{invoice_id:int}")
def invoice_detail(request: Request, invoice_id: int, session: Session = Depends(get_session)):
    return render(request, "invoices/detail.html", invoice=_get_invoice(session, invoice_id),
                  people=_people(session))


@router.get("/facturas/{invoice_id:int}/editar")
def invoice_edit(request: Request, invoice_id: int, session: Session = Depends(get_session)):
    return render(request, "invoices/form.html", invoice=_get_invoice(session, invoice_id),
                  people=_people(session), services=session.exec(select(Service)).all())


@router.post("/facturas/{invoice_id:int}/editar")
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


@router.post("/facturas/{invoice_id:int}/eliminar")
def invoice_delete(invoice_id: int, session: Session = Depends(get_session)):
    session.delete(_get_invoice(session, invoice_id))
    session.commit()
    return redirect("/facturas")


@router.get("/facturas/{invoice_id:int}/adjunto")
def invoice_attachment(invoice_id: int, session: Session = Depends(get_session)):
    invoice = _get_invoice(session, invoice_id)
    path = UPLOAD_DIR / invoice.attachment
    if not invoice.attachment or not path.is_file():
        raise HTTPException(404, "La factura no tiene adjunto")
    return FileResponse(path)


@router.post("/facturas/{invoice_id:int}/pagar")
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


@router.post("/pagos/{payment_id:int}/eliminar")
def payment_delete(payment_id: int, session: Session = Depends(get_session)):
    payment = session.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404)
    invoice_id = payment.invoice_id
    session.delete(payment)
    session.commit()
    return redirect(f"/facturas/{invoice_id}")


# --------------------------------------------------------------------------- importar PDF

def _analyze(session: Session, data: bytes) -> ParsedInvoice:
    templates = session.exec(select(InvoiceTemplate)).all()
    services = session.exec(select(Service)).all()
    return parse_invoice(extract_pdf(data), templates, services)


def _find_duplicate(session: Session, parsed: ParsedInvoice) -> Invoice | None:
    if parsed.provider_cuit and parsed.number:
        dup = session.exec(select(Invoice).where(Invoice.provider_cuit == parsed.provider_cuit,
                                                 Invoice.number == parsed.number)).first()
        if dup:
            return dup
    if parsed.amount and parsed.due_date:
        return session.exec(select(Invoice).where(
            Invoice.provider == parsed.provider, Invoice.amount == parsed.amount,
            Invoice.due_date == parsed.due_date)).first()
    return None


def _invoice_from_parsed(parsed: ParsedInvoice, attachment: str) -> Invoice:
    return Invoice(
        provider=parsed.provider, provider_cuit=parsed.provider_cuit, number=parsed.number,
        concept=parsed.template_name or parsed.provider, category=parsed.category or "Otros",
        period=parsed.period, owner_id=parsed.owner_id, service_id=parsed.service_id,
        amount=parsed.amount or 0, currency=parsed.currency,
        issue_date=parsed.issue_date or date.today(), due_date=parsed.due_date,
        account_number=parsed.account_number, payment_url=parsed.payment_url,
        payment_code=parsed.payment_code, attachment=attachment,
    )


@router.get("/facturas/importar")
def import_page(request: Request):
    return render(request, "invoices/import.html")


@router.post("/facturas/importar")
async def import_pdfs(request: Request, files: list[UploadFile] = File(...),
                      session: Session = Depends(get_session)):
    """Recibe uno o más PDFs (desde el formulario o desde "Compartir" en el celular).

    Con un solo PDF muestra el formulario precargado para revisar. Con varios, crea
    directamente las facturas que se pudieron leer completas y lista el resultado.
    """
    results = []
    for upload in files:
        data = await upload.read()
        name = upload.filename or "factura.pdf"
        if not data.startswith(b"%PDF-"):
            results.append({"name": name, "error": "No es un PDF"})
            continue
        if len(data) > MAX_PDF_BYTES:
            results.append({"name": name, "error": "El PDF pesa más de 15 MB"})
            continue
        stored = f"{uuid.uuid4().hex}.pdf"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOAD_DIR / stored).write_bytes(data)
        try:
            parsed = _analyze(session, data)
        except Exception:  # PDF dañado o protegido
            results.append({"name": name, "error": "No se pudo leer el PDF", "stored": stored})
            continue
        results.append({"name": name, "stored": stored, "parsed": parsed,
                        "duplicate": _find_duplicate(session, parsed)})

    if len(results) == 1 and "parsed" in results[0]:
        r = results[0]
        return render(request, "invoices/form.html",
                      invoice=_invoice_from_parsed(r["parsed"], r["stored"]),
                      parsed=r["parsed"], duplicate=r["duplicate"], attachment_name=r["stored"],
                      people=_people(session), services=session.exec(select(Service)).all())

    for r in results:
        parsed = r.get("parsed")
        if not parsed:
            continue
        if r["duplicate"]:
            r["error"] = "Ya estaba cargada"
        elif not parsed.complete:
            r["error"] = "Faltan datos: cargala a mano"
        else:
            invoice = _invoice_from_parsed(parsed, r["stored"])
            invoice.notes = "Importada de PDF"
            session.add(invoice)
            session.flush()
            r["invoice"] = invoice
    session.commit()
    return render(request, "invoices/import_results.html", results=results)


# --------------------------------------------------------------------------- plantillas

@router.get("/plantillas")
def template_list(request: Request, session: Session = Depends(get_session)):
    templates = session.exec(select(InvoiceTemplate).order_by(
        InvoiceTemplate.builtin, InvoiceTemplate.name)).all()
    return render(request, "invoices/templates.html", templates=templates)


@router.get("/plantillas/nueva")
def template_new(request: Request, session: Session = Depends(get_session)):
    return render(request, "invoices/template_form.html", template=InvoiceTemplate(name=""),
                  people=_people(session), services=session.exec(select(Service)).all())


@router.get("/plantillas/{template_id:int}/editar")
def template_edit(request: Request, template_id: int, session: Session = Depends(get_session)):
    template = session.get(InvoiceTemplate, template_id)
    if not template:
        raise HTTPException(404)
    return render(request, "invoices/template_form.html", template=template,
                  people=_people(session), services=session.exec(select(Service)).all())


@router.post("/plantillas/guardar")
def template_save(
    template_id: str = Form(""), name: str = Form(...), keywords: str = Form(""),
    cuit: str = Form(""), provider: str = Form(""), category: str = Form("Otros"),
    owner_id: str = Form(""), service_id: str = Form(""), amount_regex: str = Form(""),
    due_regex: str = Form(""), account_regex: str = Form(""), payment_url: str = Form(""),
    notes: str = Form(""), session: Session = Depends(get_session),
):
    for pattern in (amount_regex, due_regex, account_regex):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HTTPException(400, f"Expresión regular inválida: {pattern} ({exc})")
    template = session.get(InvoiceTemplate, int(template_id)) if template_id else InvoiceTemplate(name=name)
    if template is None:
        raise HTTPException(404)
    template.name = name.strip()
    template.keywords = keywords.strip()
    template.cuit = re.sub(r"\D", "", cuit)
    template.provider = provider.strip()
    template.category = category
    template.owner_id = opt_int(owner_id)
    template.service_id = opt_int(service_id)
    template.amount_regex = amount_regex.strip()
    template.due_regex = due_regex.strip()
    template.account_regex = account_regex.strip()
    template.payment_url = payment_url.strip()
    template.notes = notes
    session.add(template)
    session.commit()
    return redirect("/plantillas")


@router.post("/plantillas/{template_id:int}/eliminar")
def template_delete(template_id: int, session: Session = Depends(get_session)):
    template = session.get(InvoiceTemplate, template_id)
    if template:
        session.delete(template)
        session.commit()
    return redirect("/plantillas")


# --------------------------------------------------------------------------- servicios

@router.get("/servicios")
def service_list(request: Request, session: Session = Depends(get_session)):
    services = session.exec(select(Service).order_by(Service.active.desc(), Service.name)).all()
    return render(request, "invoices/services.html", services=services, people=_people(session))


@router.get("/servicios/nuevo")
def service_new(request: Request, session: Session = Depends(get_session)):
    return render(request, "invoices/service_form.html", service=Service(name=""),
                  people=_people(session))


@router.get("/servicios/{service_id:int}/editar")
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
