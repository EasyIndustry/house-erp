from datetime import date, timedelta

from sqlmodel import select

from app.models import Invoice, Person, Product, Service
from app.services import reports


def create_product(client, **extra):
    data = {"name": "Leche", "barcode": "7790080000017", "unit": "u", "min_stock": "4",
            "initial_qty": "2", "category": "Lácteos", **extra}
    r = client.post("/stock/nuevo", data=data, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_pages_render(client):
    for url in ["/", "/stock", "/stock/escanear", "/stock/compras", "/stock/nuevo",
                "/facturas", "/facturas/nueva", "/servicios", "/servicios/nuevo", "/personas"]:
        assert client.get(url).status_code == 200, url


def test_stock_flow(client, session):
    pid = create_product(client)
    product = session.get(Product, pid)
    assert product.stock == 2
    assert product.needs_restock

    client.post(f"/stock/{pid}/movimiento", data={"kind": "entrada", "quantity": "3"})
    client.post(f"/stock/{pid}/movimiento", data={"kind": "salida", "quantity": "1"})
    session.expire_all()
    assert session.get(Product, pid).stock == 4

    # Ajuste: el número es el stock real contado.
    client.post(f"/stock/{pid}/movimiento", data={"kind": "ajuste", "quantity": "10"})
    session.expire_all()
    assert session.get(Product, pid).stock == 10
    assert not session.get(Product, pid).needs_restock

    assert "Leche" in client.get(f"/stock/{pid}").text


def test_shopping_list_and_scanner_api(client, session):
    pid = create_product(client)
    items = reports.shopping_list(session)
    assert [(i.product.id, i.to_buy) for i in items] == [(pid, 2)]

    r = client.get("/api/barcode/7790080000017").json()
    assert r["exists"] and r["product"]["stock"] == 2
    assert client.post(f"/api/stock/{pid}/ajustar", json={"delta": -1}).json()["stock"] == 1

    unknown = client.get("/api/barcode/123456").json()
    assert unknown == {"exists": False, "found": False, "info": None}


def test_new_product_prefills_from_lookup(client, monkeypatch):
    from app.services.barcode import BarcodeInfo
    monkeypatch.setattr("app.routers.stock.lookup_barcode",
                        lambda code: BarcodeInfo(code, "Yerba Playadito", "Playadito", source="Open Food Facts"))
    html = client.get("/stock/nuevo?barcode=7790387000013").text
    assert "Yerba Playadito" in html and "Open Food Facts" in html


def test_duplicate_barcode_rejected(client):
    create_product(client)
    r = client.post("/stock/nuevo", data={"name": "Otra", "barcode": "7790080000017"})
    assert r.status_code == 400


def test_expiring(client, session):
    pid = create_product(client, expires_on=(date.today() + timedelta(days=3)).isoformat())
    assert [m.product_id for m in reports.expiring_soon(session, date.today())] == [pid]


def test_invoice_payments_and_balances(client, session):
    casa = session.exec(select(Person).where(Person.kind == "hogar")).one()
    client.post("/personas/guardar", data={"name": "Ana", "kind": "miembro"})
    client.post("/personas/guardar", data={"name": "Abuela", "kind": "tercero"})
    ana = session.exec(select(Person).where(Person.name == "Ana")).one()
    abuela = session.exec(select(Person).where(Person.name == "Abuela")).one()

    due = (date.today() + timedelta(days=5)).isoformat()
    r = client.post("/facturas/nueva", data={
        "provider": "Edenor", "provider_cuit": "", "number": "", "concept": "Luz",
        "category": "Luz", "period": "2026-09", "owner_id": str(abuela.id), "service_id": "",
        "amount": "30000", "currency": "ARS", "issue_date": date.today().isoformat(),
        "due_date": due, "notes": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    inv_id = int(r.headers["location"].rsplit("/", 1)[1])
    invoice = session.get(Invoice, inv_id)
    assert invoice.status == "pendiente"
    assert invoice.owner_id == abuela.id

    # Ana paga una parte de la factura de la abuela.
    client.post(f"/facturas/{inv_id}/pagar", data={"amount": "10000", "paid_by_id": str(ana.id)})
    session.expire_all()
    invoice = session.get(Invoice, inv_id)
    assert invoice.status == "parcial" and invoice.balance == 20000

    debts = reports.balances(session)
    assert [(d.debtor.name, d.creditor.name, d.amount) for d in debts] == [("Abuela", "Ana", 10000)]

    # La abuela devuelve 4000.
    client.post("/personas/saldar", data={"from_id": abuela.id, "to_id": ana.id, "amount": "4000"})
    assert reports.balances(session)[0].amount == 6000

    client.post(f"/facturas/{inv_id}/pagar", data={"amount": "20000", "paid_by_id": str(abuela.id)})
    session.expire_all()
    assert session.get(Invoice, inv_id).status == "pagada"

    spending = reports.monthly_spending(session, date.today().year, date.today().month)
    assert spending["ARS"]["Luz"] == 30000
    assert casa  # la persona "Casa" se crea sola

    csv = client.get("/exportar/facturas.csv").text
    assert "Edenor" in csv and "pagada" in csv


def test_overdue_and_services(client, session):
    client.post("/servicios/guardar", data={"name": "Internet", "provider": "Telecentro",
                                            "category": "Internet", "active": "1", "due_day": "10"})
    svc = session.exec(select(Service)).one()
    assert reports.services_missing_invoice(session, date.today()) == [svc]

    old = date.today() - timedelta(days=40)
    client.post("/facturas/nueva", data={
        "provider": "Telecentro", "provider_cuit": "30111111118", "number": "", "concept": "",
        "category": "Internet", "period": "", "owner_id": "", "service_id": str(svc.id),
        "amount": "15000", "currency": "ARS", "issue_date": old.isoformat(),
        "due_date": (old + timedelta(days=10)).isoformat(), "notes": "",
    })
    overdue, upcoming = reports.open_invoices(session, date.today())
    assert [i.provider for i in overdue] == ["Telecentro"]
    # La última factura tiene más de un mes: sigue faltando la del período actual.
    assert reports.services_missing_invoice(session, date.today()) == [svc]
    assert "Telecentro" in client.get("/").text


def test_afip_qr_endpoint_reuses_known_provider(client):
    import base64, json
    client.post("/facturas/nueva", data={
        "provider": "Metrogas", "provider_cuit": "30656800000", "number": "", "concept": "",
        "category": "Gas", "period": "", "owner_id": "", "service_id": "", "amount": "1",
        "currency": "ARS", "issue_date": "2026-08-01", "due_date": "", "notes": "",
    })
    p = base64.b64encode(json.dumps({"fecha": "2026-09-01", "cuit": 30656800000, "ptoVta": 1,
                                     "tipoCmp": 6, "nroCmp": 9, "importe": 100, "moneda": "PES"}).encode()).decode()
    data = client.post("/api/afip-qr", json={"text": f"https://www.afip.gob.ar/fe/qr/?p={p}"}).json()
    assert data["provider"] == "Metrogas" and data["category"] == "Gas" and data["amount"] == 100
    assert client.post("/api/afip-qr", json={"text": "basura"}).status_code == 400


def test_invoice_attachment(client):
    r = client.post("/facturas/nueva", data={
        "provider": "ABL", "provider_cuit": "", "number": "", "concept": "", "category": "Impuestos",
        "period": "", "owner_id": "", "service_id": "", "amount": "500", "currency": "ARS",
        "issue_date": "", "due_date": "", "notes": "",
    }, files={"attachment": ("factura.pdf", b"%PDF-1.4 test", "application/pdf")}, follow_redirects=False)
    inv_id = r.headers["location"].rsplit("/", 1)[1]
    assert client.get(f"/facturas/{inv_id}/adjunto").content == b"%PDF-1.4 test"
