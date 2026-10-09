import base64
import json

import pytest

from app.services.afip_qr import InvalidAfipQR, parse_afip_qr

PAYLOAD = {"ver": 1, "fecha": "2026-09-15", "cuit": 30500000000, "ptoVta": 12, "tipoCmp": 6,
           "nroCmp": 345678, "importe": 25430.5, "moneda": "PES", "ctz": 1, "tipoDocRec": 96,
           "nroDocRec": 30123456, "tipoCodAut": "E", "codAut": 76123456789012}


def qr_url(payload):
    p = base64.b64encode(json.dumps(payload).encode()).decode()
    return f"https://www.afip.gob.ar/fe/qr/?p={p}"


def test_parse_valid_qr():
    data = parse_afip_qr(qr_url(PAYLOAD))
    assert data["provider_cuit"] == "30500000000"
    assert data["number"] == "00012-00345678"
    assert data["comprobante"] == "Factura B"
    assert data["issue_date"] == "2026-09-15"
    assert data["amount"] == 25430.5
    assert data["currency"] == "ARS"


def test_parse_dollar_and_unpadded():
    url = qr_url({**PAYLOAD, "moneda": "DOL", "ctz": 1350}).rstrip("=")
    data = parse_afip_qr(url)
    assert data["currency"] == "USD"
    assert data["exchange_rate"] == 1350


@pytest.mark.parametrize("text", ["", "hola", "https://www.afip.gob.ar/fe/qr/?p=e30="])
def test_invalid(text):
    with pytest.raises(InvalidAfipQR):
        parse_afip_qr(text)
