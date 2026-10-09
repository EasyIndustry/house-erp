"""Genera PDFs de facturas de prueba (con texto, link de pago y QR de ARCA/AFIP)."""
import base64
import io
import json

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def afip_qr_url(**overrides) -> str:
    payload = {"ver": 1, "fecha": "2026-09-28", "cuit": 30655116202, "ptoVta": 41,
               "tipoCmp": 6, "nroCmp": 1234567, "importe": 48231.55, "moneda": "PES",
               "ctz": 1, "tipoDocRec": 96, "nroDocRec": 30111222, "tipoCodAut": "E",
               "codAut": 76398765432109, **overrides}
    return "https://www.afip.gob.ar/fe/qr/?p=" + base64.b64encode(json.dumps(payload).encode()).decode()


def make_pdf(lines: list[str], qr: str | None = None, link: str | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    if qr:
        img = qrcode.make(qr, box_size=4, border=2).get_image()
        c.drawImage(ImageReader(img), 400, 80, width=150, height=150)
    if link:
        c.drawString(40, 120, "PAGAR ONLINE")
        c.linkURL(link, (40, 115, 160, 132))
    c.save()
    return buf.getvalue()


def edenor_like() -> bytes:
    return make_pdf([
        "edenor",
        "Empresa Distribuidora y Comercializadora Norte S.A.",
        "C.U.I.T. 30-65511620-2   IVA Responsable Inscripto",
        "FACTURA B  N° 0041-01234567",
        "Fecha de emisión: 28/09/2026",
        "Nº de Cliente: 0012345678",
        "Período de consumo: 08/2026",
        "Subtotal $ 39.861,61",
        "TOTAL A PAGAR   $ 48.231,55",
        "1er Vencimiento: 14/10/2026      2do Vencimiento: 21/10/2026",
        "Próximo vencimiento: 14/12/2026",
        "Código de pago electrónico: 0012 3456 7890 12",
        "Fecha de Vto. de CAE: 08/10/2026",
    ], qr=afip_qr_url(), link="https://www.edenor.com/pagos/factura?id=abc123")


def unknown_provider() -> bytes:
    return make_pdf([
        "Cooperativa Eléctrica del Oeste Ltda. S.A.",
        "Liquidación de servicio",
        "Cuenta: 55-1234/7",
        "Período: Septiembre 2026",
        "Importe a pagar: $ 12.345,00",
        "Vence el 20 de octubre de 2026",
        "Pagá en www.coopoeste.com.ar/pagar",
    ])


def emv_field(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def edenor_real_layout() -> bytes:
    """Mismo formato que una factura real de Edenor (datos personales reemplazados)."""
    merchant = emv_field("00", "30655116202") + emv_field("01", "2")
    emv = ("000201" + emv_field("01", "12") + emv_field("43", emv_field("00", "com.auntap"))
           + emv_field("50", merchant) + emv_field("53", "032") + emv_field("58", "AR")
           + emv_field("59", "EDENOR S.A.") + emv_field("60", "Buenos Aires") + "6304ABCD")
    return make_pdf([
        "Empresa Distribuidora y Comercializadora Norte S. A.",
        "B Liquidación de Servicio Público N° 0027-11112222 Emisión 23/09/2026 C.A.B.A.",
        "C.E.S.P Nº 37382000000000 Fecha de Vencimiento: 30/09/2026",
        "Datos del cliente",
        "Cuenta 1 234 567 890",
        "PEREZ JUANA",
        "Total AL VENCIMIENTO 07/10/2026 $ 6.258,16",
        "Total AL 2do. VENCIMIENTO 13/10/2026 $ 6.283,56",
        "Hasta el 07/10/2026",
        "TOTAL A PAGAR $ 6.258,16",
        "La próxima liquidación vence el 10/11/2026",
        "Período de consumo: 25/08/2026 AL 23/09/2026",
        "• CODIGO DE PAGO: 0012 3456 7890",
        "Subtotal $ 6.258,16",
    ], qr=emv)
