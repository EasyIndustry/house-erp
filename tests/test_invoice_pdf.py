from datetime import date

from sqlalchemy import text
from sqlmodel import select

from app.models import Invoice, InvoiceTemplate, Link, Person, Service
from app.services.invoice_pdf import (ExtractedPdf, extract_pdf, find_dates, parse_invoice,
                                      parse_money, valid_cuit)
from app.services.links import links_for, parse_links
from tests.pdf_samples import (afip_qr_url, edenor_like, edenor_real_layout, make_pdf,
                               naturgy_real_layout, unknown_provider)


def templates(session):
    return session.exec(select(InvoiceTemplate)).all()


# --------------------------------------------------------------------------- unidades

def test_helpers():
    assert parse_money("$ 48.231,55") == 48231.55
    assert parse_money("1,234.50") == 1234.5
    assert parse_money("950,00") == 950
    assert find_dates("1er Vto 14/10/2026 2do 21-10-26") == [date(2026, 10, 14), date(2026, 10, 21)]
    assert find_dates("vence el 5 de noviembre de 2026") == [date(2026, 11, 5)]
    assert find_dates("Vto: 05-NOV-2026") == [date(2026, 11, 5)]
    assert valid_cuit("30-65511620-2") and not valid_cuit("30-65511620-3")


def test_extract_reads_text_links_and_qr():
    pdf = extract_pdf(edenor_like())
    assert "TOTAL A PAGAR" in pdf.text
    assert pdf.links == ["https://www.edenor.com/pagos/factura?id=abc123"]
    assert any("afip.gob.ar/fe/qr" in q for q in pdf.qr_codes)


def test_parse_known_provider(session):
    parsed = parse_invoice(extract_pdf(edenor_like()), templates(session))
    assert parsed.template_name == "Edenor" and parsed.category == "Luz"
    assert parsed.from_afip_qr
    assert parsed.provider_cuit == "30655116202"
    assert parsed.number == "00041-01234567"
    assert parsed.amount == 48231.55
    assert parsed.issue_date == date(2026, 9, 28)
    # 1er vencimiento; ignora "Próximo vencimiento" y "Vto. de CAE".
    assert parsed.due_date == date(2026, 10, 14)
    assert parsed.period == "2026-08"
    assert parsed.account_number == "0012345678"
    assert parsed.payment_codes == [("", "00123456789012")]
    assert parsed.payment_url == "https://www.edenor.com/pagos/factura?id=abc123"
    assert parsed.warnings == []


def test_parse_real_edenor_layout(session):
    """Regresión con el formato de una factura real de Edenor (sin QR de ARCA, con QR de pago)."""
    parsed = parse_invoice(extract_pdf(edenor_real_layout()), templates(session))
    assert parsed.template_name == "Edenor"
    assert parsed.provider_cuit == "30655116202"  # sacado del QR "Pagá con QR"
    assert parsed.amount == 6258.16
    # Ignora "Fecha de Vencimiento" del C.E.S.P. y "La próxima liquidación vence".
    assert parsed.due_date == date(2026, 10, 7)
    assert parsed.issue_date == date(2026, 9, 23)
    assert parsed.number == "00027-11112222"
    assert parsed.account_number == "1234567890"
    assert parsed.payment_codes == [("", "001234567890")]
    assert parsed.period == "2026-09"
    assert parsed.payment_url == "https://www.edenordigital.com"  # link de la plantilla


def test_parse_real_naturgy_layout(session):
    """Regresión con el formato de una factura real de Naturgy: sin las reglas de la plantilla
    la lectura genérica toma mal el importe y el vencimiento."""
    parsed = parse_invoice(extract_pdf(naturgy_real_layout()), templates(session))
    assert parsed.template_name == "Naturgy" and parsed.category == "Gas"
    assert parsed.provider_cuit == "30657864117"
    assert parsed.amount == 417.82
    # Ni el del C.E.S.P. (14/10) ni el "próximo vencimiento estimado" (25/11).
    assert parsed.due_date == date(2026, 10, 26)
    assert parsed.issue_date == date(2026, 10, 9)
    assert parsed.period == "2026-10"
    assert parsed.number == "00047-12345678"
    assert parsed.account_number == "1234567/1"
    assert parsed.payment_codes == [("Red Banelco", "01234567"), ("Red Link", "329001234567")]
    assert parsed.payment_url == "https://ov.naturgy.com.ar"
    assert parsed.warnings == []


def test_parse_links():
    assert parse_links("Pagar online: https://ov.naturgy.com.ar\n\nRed Link:329001234567\n"
                       "https://x.com/pagar\n0012 3456") == [
        ("Pagar online", "https://ov.naturgy.com.ar"), ("Red Link", "329001234567"),
        ("", "https://x.com/pagar"), ("", "0012 3456")]


def test_invoice_links(client, session):
    """Una factura puede tener varios links y códigos de pago (tabla `link`)."""
    r = client.post("/facturas/importar",
                    files={"files": ("gas.pdf", naturgy_real_layout(), "application/pdf")})
    assert "Red Banelco: 01234567\nRed Link: 329001234567" in r.text
    r = client.post("/facturas/nueva", data={
        "provider": "Naturgy", "amount": "417.82",
        "links": "Pagar online: https://ov.naturgy.com.ar\nRed Banelco: 01234567\nRed Link: 329001234567",
    }, follow_redirects=False)
    inv = session.get(Invoice, int(r.headers["location"].rsplit("/", 1)[1]))
    assert [(l.label, l.value, l.is_url) for l in links_for(session, inv)] == [
        ("Pagar online", "https://ov.naturgy.com.ar", True),
        ("Red Banelco", "01234567", False), ("Red Link", "329001234567", False)]
    html = client.get(f"/facturas/{inv.id}").text
    assert 'href="https://ov.naturgy.com.ar"' in html and html.count("Copiar") == 2
    assert "Red Link: 329001234567" in client.get(f"/facturas/{inv.id}/editar").text

    client.post(f"/facturas/{inv.id}/editar", data={"provider": "Naturgy", "amount": "417.82",
                                                    "links": "Red Link: 329001234567"})
    session.expire_all()
    assert [l.label for l in links_for(session, inv)] == ["Red Link"]
    client.post(f"/facturas/{inv.id}/eliminar")
    assert session.exec(select(Link)).all() == []


def test_user_template_keeps_builtin_rules(session):
    """La plantilla que crea "Recordar este proveedor" no tiene reglas: usa las de la incluida."""
    casa = session.exec(select(Person)).first()
    session.add(InvoiceTemplate(name="Naturgy", provider="Naturgy", cuit="30657864117",
                                category="Gas", owner_id=casa.id))
    session.commit()
    parsed = parse_invoice(extract_pdf(naturgy_real_layout()), templates(session))
    assert parsed.owner_id == casa.id
    assert parsed.amount == 417.82 and parsed.due_date == date(2026, 10, 26)
    assert parsed.payment_url == "https://ov.naturgy.com.ar"


def test_emv_qr_gives_provider_without_template():
    parsed = parse_invoice(extract_pdf(edenor_real_layout()), [])
    assert parsed.provider == "Edenor S.A." and parsed.provider_cuit == "30655116202"


def test_parse_without_qr_or_template(session):
    parsed = parse_invoice(extract_pdf(unknown_provider()), templates(session))
    assert parsed.template_id is None
    assert parsed.provider.startswith("Cooperativa Eléctrica")
    assert parsed.amount == 12345.0
    assert parsed.due_date == date(2026, 10, 20)
    assert parsed.period == "2026-09"
    assert parsed.account_number == "55-1234/7"
    assert parsed.payment_url == "https://www.coopoeste.com.ar/pagar"
    assert parsed.warnings  # avisa que no hay plantilla


def test_text_only_amount_and_cuit():
    text = ("AySA - Agua y Saneamientos Argentinos S.A.\nCUIT 30-70956507-5\n"
            "Subtotal 10.000,00\nTotal a pagar\n$ 12.100,00\nFecha de vencimiento\n12/11/2026")
    parsed = parse_invoice(ExtractedPdf(text=text))
    assert parsed.amount == 12100 and parsed.due_date == date(2026, 11, 12)


def test_template_regex_overrides(session):
    t = InvoiceTemplate(name="Expensas Torre", keywords="consorcio torre sol",
                        amount_regex=r"A PAGAR UF\s*([\d.]+,\d{2})", category="Expensas")
    pdf = ExtractedPdf(text="Consorcio Torre Sol\nTotal edificio 3.000.000,00\nA PAGAR UF 85.400,00\nVence 10/11/2026")
    parsed = parse_invoice(pdf, [t])
    assert parsed.template_name == "Expensas Torre" and parsed.amount == 85400


def test_service_matched_by_account_number(session):
    casa = session.exec(select(Person)).first()
    svc = Service(id=7, name="Luz depto", provider="Edenor", category="Luz",
                  account_number="0012-345678", owner_id=casa.id)
    parsed = parse_invoice(extract_pdf(edenor_like()), [], [svc])
    assert parsed.service_id == 7 and parsed.owner_id == casa.id


# --------------------------------------------------------------------------- web

def test_import_single_pdf_shows_review_and_remembers(client, session):
    client.post("/personas/guardar", data={"name": "Dueña depto", "kind": "tercero"})
    owner = session.exec(select(Person).where(Person.name == "Dueña depto")).one()

    r = client.post("/facturas/importar", files={"files": ("luz.pdf", edenor_like(), "application/pdf")})
    assert r.status_code == 200
    assert "Reconocida como" in r.text and "48231.55" in r.text and "2026-10-14" in r.text
    attachment = r.text.split('name="attachment_name" value="')[1].split('"')[0]

    r = client.post("/facturas/nueva", data={
        "attachment_name": attachment, "remember_template": "1", "provider": "Edenor",
        "provider_cuit": "30655116202", "number": "00041-01234567", "category": "Luz",
        "owner_id": str(owner.id), "amount": "48231.55", "currency": "ARS",
        "issue_date": "2026-09-28", "due_date": "2026-10-14",
        "links": "Pagar online: https://www.edenor.com/pagos/factura?id=abc123\n001234",
    }, follow_redirects=False)
    inv = session.get(Invoice, int(r.headers["location"].rsplit("/", 1)[1]))
    assert inv.attachment == attachment
    assert [l.value for l in links_for(session, inv)] == [
        "https://www.edenor.com/pagos/factura?id=abc123", "001234"]
    assert client.get(f"/facturas/{inv.id}/adjunto").content.startswith(b"%PDF")
    assert "Pagar online" in client.get(f"/facturas/{inv.id}").text

    tmpl = session.exec(select(InvoiceTemplate).where(InvoiceTemplate.cuit == "30655116202")).one()
    assert tmpl.owner_id == owner.id and not tmpl.builtin

    # La factura del mes siguiente del mismo CUIT ya viene con la dueña como titular.
    next_month = make_pdf(["edenor", "Total a pagar $ 51.000,00", "Vencimiento 14/11/2026"],
                          qr=afip_qr_url(nroCmp=1299999, importe=51000, fecha="2026-10-28"))
    r = client.post("/facturas/importar", files={"files": ("luz2.pdf", next_month, "application/pdf")})
    assert f'<option value="{owner.id}" selected' in r.text


def test_import_several_pdfs_creates_invoices(client, session):
    files = [("files", ("a.pdf", edenor_like(), "application/pdf")),
             ("files", ("b.pdf", unknown_provider(), "application/pdf")),
             ("files", ("c.txt", b"hola", "text/plain"))]
    r = client.post("/facturas/importar", files=files)
    assert r.status_code == 200 and "No es un PDF" in r.text
    providers = sorted(i.provider for i in session.exec(select(Invoice)))
    assert providers == ["Cooperativa Eléctrica del Oeste Ltda. S.A.", "Edenor"]

    # Reimportar el mismo PDF no duplica.
    r = client.post("/facturas/importar", files=[files[0], files[0]])
    assert "Ya estaba cargada" in r.text
    assert len(session.exec(select(Invoice)).all()) == 2


def test_attachment_name_cannot_escape_uploads(client, session):
    client.post("/facturas/nueva", data={"provider": "X", "amount": "1",
                                         "attachment_name": "../house.db"})
    assert session.exec(select(Invoice)).one().attachment == ""


def test_template_crud(client, session):
    r = client.post("/plantillas/guardar", data={"name": "Consorcio", "keywords": "consorcio",
                                                 "category": "Expensas", "amount_regex": "(["})
    assert r.status_code == 400
    client.post("/plantillas/guardar", data={"name": "Consorcio", "keywords": "consorcio",
                                             "category": "Expensas", "cuit": "30-11111111-8",
                                             "issue_regex": r"Emitida (\S+)",
                                             "code_regex": r"(?P<Link>\d{12})"})
    t = session.exec(select(InvoiceTemplate).where(InvoiceTemplate.name == "Consorcio")).one()
    assert t.cuit == "30111111118" and t.issue_regex == r"Emitida (\S+)"
    assert t.code_regex == r"(?P<Link>\d{12})"
    assert client.get("/plantillas").status_code == 200
    assert client.get(f"/plantillas/{t.id}/editar").status_code == 200


def test_pwa_files(client):
    assert client.get("/sw.js").headers["content-type"].startswith("application/javascript")
    manifest = client.get("/static/manifest.webmanifest").json()
    assert manifest["share_target"]["action"] == "/facturas/importar"
    assert client.get("/mas").status_code == 200
    assert client.get("/facturas/importar").status_code == 200


def test_migration_adds_new_columns():
    from app.db import add_missing_columns, engine
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE invoice DROP COLUMN account_number"))
    add_missing_columns()
    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(invoice)"))]
    assert "account_number" in cols


def test_migration_moves_payment_columns_to_links(session):
    from app.db import engine, migrate_payment_links
    session.add(Invoice(id=5, provider="Edenor", amount=1))
    session.add(Invoice(id=6, provider="Sin link", amount=1))
    session.commit()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE invoice ADD COLUMN payment_url VARCHAR DEFAULT ''"))
        conn.execute(text("ALTER TABLE invoice ADD COLUMN payment_code VARCHAR DEFAULT ''"))
        conn.execute(text("UPDATE invoice SET payment_url = 'https://www.edenordigital.com', "
                          "payment_code = '0012' WHERE id = 5"))
    migrate_payment_links()
    migrate_payment_links()  # la segunda vez no hay nada que pasar
    assert [(l.ref_table, l.ref_id, l.label, l.value) for l in session.exec(select(Link))] == [
        ("invoice", 5, "Pagar online", "https://www.edenordigital.com"),
        ("invoice", 5, "Código de pago electrónico", "0012")]
    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(invoice)"))]
    assert "payment_url" not in cols and "payment_code" not in cols
