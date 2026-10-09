"""Links y códigos sueltos de cualquier registro (tabla comodín `link`).

Cada fila apunta a su dueño por nombre de tabla + id, así sirve para facturas, servicios,
productos o lo que haga falta sin agregar columnas. En los formularios se editan como texto,
uno por renglón: `Etiqueta: valor`.
"""
import re

from sqlmodel import Session, select

from app.models import Link

LINE_RE = re.compile(r"([^:/]{1,40}):\s*(?!//)(.+)")


def links_for(session: Session, obj) -> list[Link]:
    return session.exec(select(Link).where(
        Link.ref_table == obj.__tablename__, Link.ref_id == obj.id).order_by(Link.id)).all()


def set_links(session: Session, obj, items: list[tuple[str, str]]) -> None:
    """Reemplaza los links del registro por `items`, una lista de (etiqueta, valor)."""
    for link in links_for(session, obj):
        session.delete(link)
    for label, value in items:
        session.add(Link(ref_table=obj.__tablename__, ref_id=obj.id, label=label, value=value))


def parse_links(text: str) -> list[tuple[str, str]]:
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = LINE_RE.fullmatch(line)
        items.append((m.group(1).strip(), m.group(2).strip()) if m else ("", line))
    return items


def links_text(items) -> str:
    """Inverso de `parse_links`; acepta filas `Link` o pares (etiqueta, valor)."""
    pairs = [(i.label, i.value) if isinstance(i, Link) else i for i in items]
    return "\n".join(f"{label}: {value}" if label else value for label, value in pairs)
