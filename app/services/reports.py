"""Cálculos que alimentan el tablero: alertas de stock, vencimientos, deudas y gastos."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import (FREQUENCIES, Invoice, Payment, Person, Product, Service,
                        Settlement, StockMovement)


@dataclass
class ShoppingItem:
    product: Product
    to_buy: float


def shopping_list(session: Session) -> list[ShoppingItem]:
    items = [
        ShoppingItem(p, round(p.min_stock - p.stock, 3))
        for p in session.exec(select(Product).order_by(Product.category, Product.name))
        if p.needs_restock
    ]
    return items


def expiring_soon(session: Session, today: date, days: int = 7) -> list[StockMovement]:
    """Entradas con vencimiento dentro de `days` días de productos que todavía tienen stock."""
    limit = today + timedelta(days=days)
    rows = session.exec(
        select(StockMovement)
        .where(StockMovement.expires_on.is_not(None), StockMovement.expires_on <= limit)
        .order_by(StockMovement.expires_on)
    ).all()
    return [m for m in rows if m.product and m.product.stock > 0]


def open_invoices(session: Session, today: date, days: int = 10) -> tuple[list[Invoice], list[Invoice]]:
    """(vencidas, por vencer en los próximos `days` días)."""
    overdue, upcoming = [], []
    for inv in session.exec(select(Invoice).order_by(Invoice.due_date)):
        status = inv.status_on(today)
        if status == "vencida":
            overdue.append(inv)
        elif status in ("pendiente", "parcial") and (
            inv.due_date is None or inv.due_date <= today + timedelta(days=days)
        ):
            upcoming.append(inv)
    return overdue, upcoming


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, 28))


def services_missing_invoice(session: Session, today: date) -> list[Service]:
    """Servicios activos cuya última factura es más vieja que su frecuencia."""
    missing = []
    for svc in session.exec(select(Service).where(Service.active == True)):  # noqa: E712
        months = FREQUENCIES.get(svc.frequency, 1)
        last = max((i.issue_date for i in svc.invoices), default=None)
        if last is None or _add_months(last, months) <= today:
            missing.append(svc)
    return missing


def monthly_spending(session: Session, year: int, month: int) -> dict[str, dict[str, float]]:
    """Gasto efectivamente pagado en el mes, por moneda y categoría (incluye compras de stock)."""
    start = date(year, month, 1)
    end = _add_months(start, 1).replace(day=1)
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for pay in session.exec(select(Payment).where(Payment.date >= start, Payment.date < end)):
        inv = pay.invoice
        totals[inv.currency][inv.category] += pay.amount
    for mov in session.exec(
        select(StockMovement).where(
            StockMovement.kind == "entrada", StockMovement.date >= start,
            StockMovement.date < end, StockMovement.unit_price.is_not(None),
        )
    ):
        totals["ARS"]["Compras de stock"] += mov.quantity * mov.unit_price
    return {cur: dict(sorted(cats.items(), key=lambda kv: -kv[1])) for cur, cats in totals.items()}


@dataclass
class Debt:
    debtor: Person
    creditor: Person
    amount: float
    currency: str


def balances(session: Session) -> list[Debt]:
    """Quién le debe a quién: pagos hechos por una persona sobre facturas de otra, menos lo saldado."""
    people = {p.id: p for p in session.exec(select(Person))}
    # net[(a, b, cur)] > 0 significa que `a` le debe a `b`.
    net: dict[tuple[int, int, str], float] = defaultdict(float)

    def add(debtor: int, creditor: int, currency: str, amount: float) -> None:
        if debtor == creditor:
            return
        a, b = sorted((debtor, creditor))
        net[(a, b, currency)] += amount if debtor == a else -amount

    for pay in session.exec(select(Payment)):
        inv = pay.invoice
        if pay.paid_by_id and inv.owner_id:
            add(inv.owner_id, pay.paid_by_id, inv.currency, pay.amount)
    for s in session.exec(select(Settlement)):
        add(s.to_id, s.from_id, s.currency, s.amount)

    debts = []
    for (a, b, cur), amount in net.items():
        amount = round(amount, 2)
        if abs(amount) < 0.01:
            continue
        debtor, creditor = (a, b) if amount > 0 else (b, a)
        debts.append(Debt(people[debtor], people[creditor], abs(amount), cur))
    return sorted(debts, key=lambda d: (d.currency, -d.amount))
