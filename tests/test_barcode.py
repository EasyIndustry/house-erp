import httpx

from app.services.barcode import lookup_barcode, normalize_barcode


def make_client(routes):
    def handler(request: httpx.Request):
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=response)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_normalize():
    assert normalize_barcode(" 779-0895 000997 ") == "7790895000997"


def test_open_food_facts_hit():
    client = make_client({
        "https://world.openfoodfacts.org": {
            "status": 1,
            "product": {"product_name": "Coca-Cola", "product_name_es": "Coca-Cola Sabor Original",
                        "brands": "Coca-Cola, The Coca-Cola Company", "quantity": "2.25 l",
                        "categories_tags": ["en:beverages", "en:sodas"],
                        "image_front_url": "https://img/coca.jpg"},
        }
    })
    info = lookup_barcode("7790895000997", client=client)
    assert info.name == "Coca-Cola Sabor Original"
    assert info.brand == "Coca-Cola"
    assert info.category == "Bebidas"
    assert info.package_size == "2.25 l"
    assert info.source == "Open Food Facts"


def test_falls_back_to_other_sources():
    client = make_client({
        "https://world.openfoodfacts.org": {"status": 0},
        "https://world.openbeautyfacts.org": {"status": 0},
        "https://world.openproductsfacts.org": {"status": 0},
        "https://api.upcitemdb.com": {"items": [{"title": "Lavandina 1L", "brand": "Ayudín",
                                                  "images": ["https://img/a.jpg"]}]},
    })
    info = lookup_barcode("7790520985231", client=client)
    assert info.name == "Lavandina 1L"
    assert info.source == "UPCitemdb"


def test_not_found_and_network_errors():
    def boom(request):
        raise httpx.ConnectError("sin red")
    client = httpx.Client(transport=httpx.MockTransport(boom))
    assert lookup_barcode("123", client=client) is None
    assert lookup_barcode("", client=client) is None
