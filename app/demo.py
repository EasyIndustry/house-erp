"""Carga datos de ejemplo para probar la app: `python -m app.demo`."""
from datetime import date, timedelta

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Invoice, Location, Payment, Person, Product, Service, StockMovement


def main() -> None:
    init_db()
    with Session(engine) as s:
        if s.exec(select(Product)).first():
            print("La base ya tiene datos; no cargo el demo.")
            return
        today = date.today()
        casa = s.exec(select(Person).where(Person.kind == "hogar")).one()
        ana, juan, abuela = Person(name="Ana"), Person(name="Juan"), Person(name="Abuela Marta", kind="tercero")
        s.add_all([ana, juan, abuela])
        loc = {l.name: l for l in s.exec(select(Location))}

        for name, brand, cat, unit, place, qty, minimum, expires in [
            ("Leche entera 1 l", "La Serenísima", "Lácteos", "u", "Heladera", 2, 4, today + timedelta(days=4)),
            ("Yerba mate 1 kg", "Playadito", "Almacén", "u", "Alacena", 1, 2, None),
            ("Fideos spaghetti 500 g", "Lucchetti", "Almacén", "u", "Alacena", 6, 3, None),
            ("Detergente 750 ml", "Magistral", "Limpieza", "u", "Lavadero", 1, 1, None),
            ("Papel higiénico x4", "Higienol", "Higiene personal", "paquete", "Baño", 3, 2, None),
        ]:
            p = Product(name=name, brand=brand, category=cat, unit=unit, min_stock=minimum,
                        location_id=loc[place].id)
            s.add(p)
            s.flush()
            s.add(StockMovement(product_id=p.id, kind="entrada", quantity=qty, expires_on=expires))

        luz = Service(name="Luz casa", provider="Edenor", category="Luz", owner_id=casa.id,
                      frequency="bimestral", due_day=18, expected_amount=42000)
        internet = Service(name="Internet", provider="Telecentro", category="Internet",
                           owner_id=casa.id, due_day=10, expected_amount=21000)
        s.add_all([luz, internet])
        s.flush()

        last_month = today.replace(day=1) - timedelta(days=1)
        inv1 = Invoice(provider="Edenor", concept="Luz casa", category="Luz", owner_id=casa.id,
                       service_id=luz.id, amount=40850, issue_date=today - timedelta(days=5),
                       due_date=today + timedelta(days=6), period=today.strftime("%Y-%m"))
        inv2 = Invoice(provider="Telecentro", concept="Internet", category="Internet",
                       owner_id=casa.id, service_id=internet.id, amount=20990,
                       issue_date=last_month.replace(day=1), due_date=last_month.replace(day=10),
                       period=last_month.strftime("%Y-%m"))
        inv3 = Invoice(provider="Metrogas", concept="Gas de la abuela", category="Gas",
                       owner_id=abuela.id, amount=18500, issue_date=today - timedelta(days=2),
                       due_date=today + timedelta(days=12), period=today.strftime("%Y-%m"))
        s.add_all([inv1, inv2, inv3])
        s.flush()
        s.add(Payment(invoice_id=inv3.id, amount=18500, paid_by_id=ana.id, method="Mercado Pago"))
        s.commit()
        print("Datos de ejemplo cargados.")


if __name__ == "__main__":
    main()
