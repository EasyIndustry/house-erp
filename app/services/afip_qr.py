"""Lectura del código QR de las facturas electrónicas argentinas (ARCA, ex AFIP).

Desde 2021 toda factura electrónica trae un QR con una URL del estilo
``https://www.afip.gob.ar/fe/qr/?p=<JSON en base64>``. El JSON trae fecha, CUIT del
emisor, punto de venta, tipo y número de comprobante, importe y moneda, que usamos
para precargar la factura sin tipear nada.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

COMPROBANTE_TYPES = {
    1: "Factura A", 2: "Nota de débito A", 3: "Nota de crédito A",
    6: "Factura B", 7: "Nota de débito B", 8: "Nota de crédito B",
    11: "Factura C", 12: "Nota de débito C", 13: "Nota de crédito C",
    51: "Factura M", 201: "Factura de crédito electrónica A",
    206: "Factura de crédito electrónica B", 211: "Factura de crédito electrónica C",
}
CURRENCY_MAP = {"PES": "ARS", "DOL": "USD"}


class InvalidAfipQR(ValueError):
    pass


def _decode_payload(p: str) -> dict:
    p = p.strip().replace("-", "+").replace("_", "/")
    p += "=" * (-len(p) % 4)
    try:
        return json.loads(base64.b64decode(p).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidAfipQR("El QR no contiene datos de ARCA/AFIP válidos") from exc


def parse_afip_qr(text: str) -> dict:
    """Recibe el texto leído del QR (la URL) y devuelve los campos para la factura."""
    text = text.strip()
    query = parse_qs(urlparse(text).query)
    payload = query.get("p", [None])[0]
    if payload is None:
        # Algunos lectores devuelven sólo el parámetro.
        payload = text
    data = _decode_payload(payload)
    if not isinstance(data, dict) or "cuit" not in data or "importe" not in data:
        raise InvalidAfipQR("El QR no contiene datos de ARCA/AFIP válidos")

    tipo = int(data.get("tipoCmp", 0))
    pto_vta = int(data.get("ptoVta", 0))
    nro = int(data.get("nroCmp", 0))
    try:
        issue_date = date.fromisoformat(str(data.get("fecha", ""))[:10]).isoformat()
    except ValueError:
        issue_date = ""

    return {
        "provider_cuit": str(data["cuit"]),
        "number": f"{pto_vta:05d}-{nro:08d}",
        "comprobante": COMPROBANTE_TYPES.get(tipo, f"Comprobante tipo {tipo}"),
        "issue_date": issue_date,
        "amount": float(data["importe"]),
        "currency": CURRENCY_MAP.get(str(data.get("moneda", "PES")).upper(), "ARS"),
        "exchange_rate": float(data.get("ctz", 1) or 1),
        "cae": str(data.get("codAut", "")),
    }
