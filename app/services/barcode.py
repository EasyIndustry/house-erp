"""Búsqueda de productos por código de barras en APIs públicas.

Orden de consulta:
1. Open Food Facts      (alimentos y bebidas, muy buena cobertura en Argentina)
2. Open Beauty Facts    (higiene y cosmética)
3. Open Products Facts  (limpieza, bazar y otros)
4. UPCitemdb (trial)    (catálogo general, 100 consultas/día sin API key)

Todas son gratuitas y no requieren clave.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import httpx

USER_AGENT = "HouseERP/0.1 (https://github.com/easyindustry/house-erp)"
OFF_FIELDS = "product_name,product_name_es,generic_name_es,brands,categories_tags,image_front_url,image_url,quantity"
OPEN_FACTS_HOSTS = [
    ("Open Food Facts", "https://world.openfoodfacts.org"),
    ("Open Beauty Facts", "https://world.openbeautyfacts.org"),
    ("Open Products Facts", "https://world.openproductsfacts.org"),
]
UPCITEMDB_URL = "https://api.upcitemdb.com/prod/trial/lookup"

# Mapeo de categorías de Open Food Facts (tags en inglés) a las categorías de la casa.
CATEGORY_HINTS = [
    ("beverages", "Bebidas"), ("waters", "Bebidas"), ("dairies", "Lácteos"),
    ("milks", "Lácteos"), ("cheeses", "Lácteos"), ("yogurts", "Lácteos"),
    ("meats", "Carnes"), ("frozen", "Congelados"), ("fruits", "Verdulería"),
    ("vegetables", "Verdulería"), ("pet-food", "Mascotas"), ("cleaning", "Limpieza"),
    ("detergents", "Limpieza"), ("shampoo", "Higiene personal"), ("toothpaste", "Higiene personal"),
    ("soaps", "Higiene personal"),
]


@dataclass
class BarcodeInfo:
    barcode: str
    name: str
    brand: str = ""
    category: str = ""
    package_size: str = ""
    image_url: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_barcode(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit())


def _guess_category(tags: list[str]) -> str:
    joined = " ".join(tags or [])
    for hint, category in CATEGORY_HINTS:
        if hint in joined:
            return category
    return "Almacén" if tags else ""


def _from_open_facts(code: str, data: dict, source: str) -> BarcodeInfo | None:
    if data.get("status") != 1 or not data.get("product"):
        return None
    p = data["product"]
    name = p.get("product_name_es") or p.get("product_name") or p.get("generic_name_es") or ""
    if not name:
        return None
    brand = (p.get("brands") or "").split(",")[0].strip()
    return BarcodeInfo(
        barcode=code,
        name=name.strip(),
        brand=brand,
        category=_guess_category(p.get("categories_tags") or []),
        package_size=p.get("quantity") or "",
        image_url=p.get("image_front_url") or p.get("image_url") or "",
        source=source,
    )


def _from_upcitemdb(code: str, data: dict) -> BarcodeInfo | None:
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    images = item.get("images") or []
    return BarcodeInfo(
        barcode=code,
        name=item.get("title", "").strip(),
        brand=item.get("brand", ""),
        category="",
        package_size=item.get("size", ""),
        image_url=images[0] if images else "",
        source="UPCitemdb",
    )


def lookup_barcode(code: str, client: httpx.Client | None = None) -> BarcodeInfo | None:
    """Devuelve la info del producto o None si ninguna API lo conoce (o no hay red)."""
    code = normalize_barcode(code)
    if not code:
        return None
    own_client = client is None
    client = client or httpx.Client(timeout=6.0, headers={"User-Agent": USER_AGENT})
    try:
        for source, host in OPEN_FACTS_HOSTS:
            try:
                r = client.get(f"{host}/api/v2/product/{code}.json", params={"fields": OFF_FIELDS})
                if r.status_code == 200:
                    info = _from_open_facts(code, r.json(), source)
                    if info:
                        return info
            except (httpx.HTTPError, ValueError):
                continue
        try:
            r = client.get(UPCITEMDB_URL, params={"upc": code})
            if r.status_code == 200:
                return _from_upcitemdb(code, r.json())
        except (httpx.HTTPError, ValueError):
            pass
        return None
    finally:
        if own_client:
            client.close()
