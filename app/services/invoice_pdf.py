"""Lectura automática de facturas de servicios en PDF.

Se combinan tres fuentes de datos del PDF:

1. El **texto** (las facturas digitales lo traen; las escaneadas no).
2. Los **links** del PDF (muchos traen un botón "Pagar online").
3. Los **códigos QR** de cada página: el QR de ARCA/AFIP (CUIT, número, fecha, importe
   exactos) y a veces un QR de pago.

Primero se identifica al proveedor con las plantillas (por CUIT o palabras clave) y después
se extraen los campos con reglas genéricas, que cada plantilla puede reemplazar con sus
propias expresiones regulares.
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.services.afip_qr import InvalidAfipQR, parse_afip_qr

# (nombre, palabras clave, categoría). Se cargan como plantillas editables en la base.
BUILTIN_TEMPLATES = [
    ("Edenor", "edenor, empresa distribuidora y comercializadora norte", "Luz"),
    ("Edesur", "edesur", "Luz"),
    ("Edelap", "edelap", "Luz"),
    ("EPEC", "epec, empresa provincial de energia de cordoba", "Luz"),
    ("AySA", "aysa, agua y saneamientos argentinos", "Agua"),
    ("ABSA", "aguas bonaerenses", "Agua"),
    ("Metrogas", "metrogas", "Gas"),
    ("Naturgy", "naturgy, gas natural ban", "Gas"),
    ("Camuzzi", "camuzzi", "Gas"),
    ("Ecogas", "ecogas, distribuidora de gas del centro", "Gas"),
    ("Telecentro", "telecentro", "Internet"),
    ("Personal / Fibertel", "telecom argentina, fibertel", "Internet"),
    ("Movistar", "movistar, telefonica de argentina, telefonica moviles", "Telefonía"),
    ("Claro", "amx argentina", "Telefonía"),
    ("DirecTV", "directv", "Cable/Streaming"),
    ("ABL CABA (AGIP)", "agip, administracion gubernamental de ingresos publicos", "Impuestos"),
    ("ARBA", "arba, agencia de recaudacion de la provincia de buenos aires", "Impuestos"),
    ("Expensas", "expensas, liquidacion de expensas", "Expensas"),
]

# Reglas propias de algunas plantillas incluidas (tomadas de sus facturas reales): link de
# pago por defecto y expresiones regulares para los datos que la lectura genérica no encuentra.
BUILTIN_RULES = {
    "Edenor": {"payment_url": "https://www.edenordigital.com"},
    # En el PDF de Naturgy los valores vienen separados de sus etiquetas: se ubican por posición.
    "Naturgy": {
        "payment_url": "https://ov.naturgy.com.ar",
        "amount_regex": r"^\$[ \t]*([\d.]+,\d{2})[ \t]*$",
        "due_regex": r"C\.I\.F\.[^\n]*\n\s*(\d{2}/\d{2}/\d{2,4})",
        "issue_regex": r"C\.I\.F\.\D+\d+\s+(\d{2}/\d{2}/\d{2,4})",
        "account_regex": r"^(\d{5,8}/\d{1,2})[ \t]*$",
        "code_regex": r"^(?P<Red_Banelco>\d{8})[ \t]*\n(?:.*\n)?(?P<Red_Link>329\d{9})[ \t]*$",
    },
}

MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

AMOUNT_KEYWORDS = [
    "total a pagar", "importe a pagar", "total a abonar", "monto a pagar", "saldo a pagar",
    "importe a abonar", "su pago", "total factura", "importe total", "total del periodo", "total",
]
DUE_KEYWORDS = ["vencimiento", "vence", "vto", "pagar hasta", "fecha limite de pago"]
DUE_EXCLUDE = ["cae", "c.e.s.p", "cesp", "proxim", "prox.", "siguiente", "transcurrida"]
ISSUE_KEYWORDS = ["fecha de emision", "emision", "fecha de factura", "fecha factura"]
ACCOUNT_KEYWORDS = [
    "numero de cliente", "nro. de cliente", "nro de cliente", "n° de cliente", "nº de cliente",
    "cliente n", "nro. cliente", "nro cliente", "numero de cuenta", "nro. de cuenta",
    "nro de cuenta", "n° de cuenta", "nº de cuenta", "no de cliente", "no de cuenta",
    "cuenta n", "cuenta:", "cliente:", "suministro", "cuenta",
    "nis", "numero de usuario", "partida", "unidad funcional",
]
PAYMENT_CODE_KEYWORDS = [
    "codigo de pago electronico", "cod. pago electronico", "codigo de pago", "codigo link",
    "pago mis cuentas", "codigo para pagar",
]

MONEY_RE = re.compile(
    r"(?<![\d.,])\$?\s*(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?![\d,]|\.\d)"
)
NUM_DATE_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")
TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:\s+de)?[\s/.-]+([a-z]{3})[a-z]*\.?(?:\s+de)?[\s/.-]+(\d{4}|\d{2})\b"
)
PERIOD_RE = re.compile(r"\b(0?[1-9]|1[0-2])\s*[/-]\s*(20\d{2})\b")
PERIOD_TEXT_RE = re.compile(r"\b([a-z]{3})[a-z]*\.?(?:\s+de)?[\s/-]*(20\d{2})\b")
CUIT_RE = re.compile(r"\b(20|23|24|27|30|33|34)-?(\d{8})-?(\d)\b")
NUMBER_RE = re.compile(r"\b(\d{4,5})\s*-\s*(\d{8})\b")
# Punto de venta y número pegados, después del tipo de comprobante (sobre texto sin acentos).
LSP_NUMBER_RE = re.compile(r"liquidacion de servicios? publicos?\D{0,12}(\d{5})(\d{8})\b")
URL_RE = re.compile(r"(https?://[^\s<>\"')]+|www\.[^\s<>\"')]+)", re.IGNORECASE)


@dataclass
class ExtractedPdf:
    text: str = ""
    links: list[str] = field(default_factory=list)
    qr_codes: list[str] = field(default_factory=list)


@dataclass
class ParsedInvoice:
    provider: str = ""
    provider_cuit: str = ""
    category: str = ""
    number: str = ""
    account_number: str = ""
    amount: float | None = None
    currency: str = "ARS"
    issue_date: date | None = None
    due_date: date | None = None
    period: str = ""
    payment_url: str = ""
    payment_codes: list[tuple[str, str]] = field(default_factory=list)  # (etiqueta, código)
    template_id: int | None = None
    template_name: str = ""
    service_id: int | None = None
    owner_id: int | None = None
    from_afip_qr: bool = False
    text: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return bool(self.provider and self.amount)

    @property
    def links(self) -> list[tuple[str, str]]:
        """Link y códigos de pago como (etiqueta, valor), para guardarlos con la factura."""
        url = [("Pagar online", self.payment_url)] if self.payment_url else []
        return url + [(label or "Código de pago electrónico", code)
                      for label, code in self.payment_codes]


# --------------------------------------------------------------------------- PDF → datos

def extract_pdf(data: bytes, max_qr_pages: int = 3) -> ExtractedPdf:
    """Saca texto, links y códigos QR del PDF. Nunca lanza por un PDF raro: devuelve lo que pudo."""
    from pypdf import PdfReader

    result = ExtractedPdf()
    reader = PdfReader(io.BytesIO(data))
    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:  # pragma: no cover - PDFs mal formados
            pages_text.append("")
        for annot in page.get("/Annots") or []:
            try:
                obj = annot.get_object()
                uri = obj.get("/A", {}).get("/URI")
                if uri:
                    result.links.append(str(uri))
            except Exception:  # pragma: no cover
                continue
    result.text = "\n".join(pages_text)
    result.qr_codes = _read_qr_codes(data, max_qr_pages)
    return result


def _read_qr_codes(data: bytes, max_pages: int) -> list[str]:
    try:
        import pypdfium2 as pdfium
        import zxingcpp
    except ImportError:  # pragma: no cover - dependencias opcionales
        return []
    codes: list[str] = []
    pdf = pdfium.PdfDocument(data)
    try:
        for index in range(min(len(pdf), max_pages)):
            image = pdf[index].render(scale=3).to_pil()
            for barcode in zxingcpp.read_barcodes(image):
                if barcode.text and barcode.text not in codes:
                    codes.append(barcode.text)
    finally:
        pdf.close()
    return codes


# --------------------------------------------------------------------------- helpers de texto

def fold(text: str) -> str:
    """minúsculas y sin acentos, para comparar."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def parse_money(raw: str) -> float:
    raw = raw.replace("$", "").strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2}", raw):
        return float(raw.replace(",", ""))
    return float(raw.replace(".", "").replace(",", "."))


def _year(value: str) -> int:
    y = int(value)
    return y + 2000 if y < 100 else y


def find_dates(line: str) -> list[date]:
    found: list[tuple[int, date]] = []
    for m in NUM_DATE_RE.finditer(line):
        try:
            found.append((m.start(), date(_year(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    for m in TEXT_DATE_RE.finditer(fold(line)):
        month = MONTHS.get(m.group(2))
        if month:
            try:
                found.append((m.start(), date(_year(m.group(3)), month, int(m.group(1)))))
            except ValueError:
                pass
    return [d for _, d in sorted(found, key=lambda x: x[0])]


def valid_cuit(cuit: str) -> bool:
    digits = [int(c) for c in cuit if c.isdigit()]
    if len(digits) != 11:
        return False
    weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    check = 11 - sum(d * w for d, w in zip(digits, weights)) % 11
    check = {11: 0, 10: 9}.get(check, check)
    return check == digits[10]


def _lines(text: str) -> list[str]:
    return [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _after_keyword(lines: list[str], folded: list[str], keywords: list[str], finder,
                   exclude: list[str] = (), lookahead: int = 2):
    """Busca el primer valor que `finder` encuentre en la línea de alguna palabra clave
    (después de la palabra) o en las siguientes `lookahead` líneas. Las palabras clave se
    prueban en orden de prioridad."""
    for kw in keywords:
        kw_re = re.compile(r"(?<![a-z0-9])" + re.escape(fold(kw)))
        for i, fl in enumerate(folded):
            m = kw_re.search(fl)
            if not m or any(ex in fl for ex in exclude):
                continue
            rest = lines[i][m.end():]
            value = finder(rest)
            if value:
                return value
            for j in range(i + 1, min(i + 1 + lookahead, len(lines))):
                if any(ex in folded[j] for ex in exclude):
                    break
                value = finder(lines[j])
                if value:
                    return value
    return None


def _first_money(s: str):
    m = MONEY_RE.search(s)
    return parse_money(m.group(1)) if m else None


def _account_token(s: str):
    m = re.match(r"[\s:#°º.nro]*([0-9](?:[0-9\-/.]| (?=\d{3}))*[0-9])", s, re.IGNORECASE)
    if not m or NUM_DATE_RE.fullmatch(m.group(1)):
        return None
    token = m.group(1).replace(" ", "")
    return token if sum(c.isdigit() for c in token) >= 4 else None


def _digits_token(s: str):
    m = re.search(r"(\d[\d ]{5,}\d)", s)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def _regex_match(pattern: str, text: str):
    if not pattern:
        return None
    try:
        return re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    except re.error:
        return None


def _regex_value(pattern: str, text: str):
    m = _regex_match(pattern, text)
    if not m:
        return None
    return (m.group(1) if m.groups() else m.group(0)).strip()


def _regex_codes(pattern: str, text: str) -> list[tuple[str, str]]:
    """Códigos de pago de la plantilla, como (etiqueta, código). Si la expresión tiene grupos
    con nombre devuelve uno por cada uno, con el nombre como etiqueta (`Red_Link` → "Red Link")."""
    m = _regex_match(pattern, text)
    if not m:
        return []
    named = [(k.replace("_", " "), v.strip()) for k, v in m.groupdict().items() if v]
    return named or [("", _regex_value(pattern, text))]


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip(".,;")
    return url if url.lower().startswith("http") else f"https://{url}"


def _pick_payment_url(candidates: list[str]) -> str:
    urls = []
    for c in candidates:
        if not URL_RE.match(c.strip()):
            continue
        url = _normalize_url(c)
        if "afip.gob.ar" in url or url in urls:
            continue
        urls.append(url)
    if not urls:
        return ""
    keywords = ("pag", "pay", "mercadopago", "mpago", "link.com.ar", "botondepago", "cobro", "abonar")
    return next((u for u in urls if any(k in u.lower() for k in keywords)), "")


def _guess_provider_name(lines: list[str]) -> str:
    for line in lines[:15]:
        if re.search(r"\bS\.?\s?A\.?(\s?U\.?)?\b|\bS\.?R\.?L\.?\b", line) and len(line) < 70:
            return re.sub(r"\s+", " ", line).strip(" -")
    return lines[0][:60] if lines else ""


def _find_period(lines: list[str], folded: list[str]) -> str:
    for kw in ("periodo", "mes de consumo", "liquidacion"):
        for i, fl in enumerate(folded):
            if kw not in fl:
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            dates = find_dates(lines[i]) or find_dates(nxt)
            if dates:
                return max(dates).strftime("%Y-%m")
            chunk = fl + " " + fold(nxt)
            m = PERIOD_RE.search(chunk)
            if m:
                return f"{m.group(2)}-{int(m.group(1)):02d}"
            m = PERIOD_TEXT_RE.search(chunk)
            if m and m.group(1) in MONTHS:
                return f"{m.group(2)}-{MONTHS[m.group(1)]:02d}"
    return ""


def parse_emv_qr(payload: str) -> dict:
    """QR de pago interoperable (estándar EMVCo, el "Pagá con QR" de las facturas).

    Devuelve los campos de primer nivel ({"59": "EDENOR S.A.", ...}) o {} si no es EMVCo.
    """
    if not payload.startswith("000201"):
        return {}
    return _parse_tlv(payload)


def _parse_tlv(payload: str) -> dict:
    fields, i = {}, 0
    while i + 4 <= len(payload):
        tag, length = payload[i:i + 2], payload[i + 2:i + 4]
        if not length.isdigit():
            return {}
        end = i + 4 + int(length)
        if end > len(payload):
            return {}
        fields[tag] = payload[i + 4:end]
        i = end
    return fields


def _emv_cuit(fields: dict) -> str:
    """CUIT del comercio: en Argentina va en alguna plantilla 26-51 como un número de 11 dígitos."""
    for tag in sorted(fields):
        if "26" <= tag <= "51":
            for value in _parse_tlv(fields[tag]).values():
                if len(value) == 11 and value[:2] in ("30", "33", "34") and valid_cuit(value):
                    return value
    return ""


# --------------------------------------------------------------------------- análisis

def match_template(text: str, cuit: str, templates: list) -> object | None:
    """Plantilla del proveedor: primero por CUIT, después por palabra clave (las del usuario
    antes que las incluidas, y la palabra más larga gana)."""
    if cuit:
        for t in templates:
            if t.cuit and re.sub(r"\D", "", t.cuit) == cuit:
                return t
    folded = fold(text)
    best, best_score = None, (-1, -1)
    for t in templates:
        for kw in t.keyword_list:
            if re.search(rf"(?<![a-z0-9]){re.escape(fold(kw))}(?![a-z0-9])", folded):
                score = (0 if t.builtin else 1, len(kw))
                if score > best_score:
                    best, best_score = t, score
    return best


def builtin_rules(template, templates: list) -> object | None:
    """Plantilla incluida del mismo proveedor que una del usuario (las de "Recordar este
    proveedor" no traen reglas de lectura propias)."""
    if template is None or template.builtin:
        return None
    name = fold(template.provider or template.name)
    return next((t for t in templates if t.builtin and fold(t.provider or t.name) == name), None)


def match_service(text: str, services: list, provider: str = ""):
    """Servicio cuyo nº de cliente aparece en la factura (o el único activo de ese proveedor)."""
    for svc in services:
        digits = re.sub(r"\D", "", svc.account_number or "")
        if len(digits) >= 4:
            pattern = r"(?<!\d)" + r"[\s\-./]?".join(digits) + r"(?!\d)"
            if re.search(pattern, text):
                return svc
    if provider:
        same = [s for s in services if s.active and fold(s.provider or s.name) == fold(provider)]
        if len(same) == 1:
            return same[0]
    return None


def parse_invoice(pdf: ExtractedPdf, templates: list = (), services: list = ()) -> ParsedInvoice:
    text = pdf.text
    lines = _lines(text)
    folded = [fold(l) for l in lines]
    result = ParsedInvoice(text=text)

    # 1) QR de ARCA/AFIP: datos oficiales.
    afip = None
    for code in pdf.qr_codes:
        if "afip" in code.lower() or "arca" in code.lower():
            try:
                afip = parse_afip_qr(code)
                break
            except InvalidAfipQR:
                continue
    if afip:
        result.from_afip_qr = True
        result.provider_cuit = afip["provider_cuit"]
        result.number = afip["number"]
        result.amount = afip["amount"]
        result.currency = afip["currency"]
        result.issue_date = date.fromisoformat(afip["issue_date"]) if afip["issue_date"] else None

    # 1b) QR de pago interoperable: CUIT y nombre del comercio.
    emv = next((f for f in map(parse_emv_qr, pdf.qr_codes) if f), {})
    if emv and not result.provider_cuit:
        result.provider_cuit = _emv_cuit(emv)
    if emv.get("54") and result.amount is None:
        try:
            result.amount = float(emv["54"])
        except ValueError:
            pass

    # 2) CUIT del emisor por texto (el primero válido de empresa).
    if not result.provider_cuit:
        cuits = ["".join(m.groups()) for m in CUIT_RE.finditer(text)]
        cuits = [c for c in cuits if valid_cuit(c)]
        company = [c for c in cuits if c[:2] in ("30", "33", "34")]
        result.provider_cuit = (company or cuits or [""])[0]

    # 3) Plantilla del proveedor.
    template = match_template(text, result.provider_cuit, list(templates))
    if template:
        result.template_id = template.id
        result.template_name = template.name
        result.provider = template.provider or template.name
        result.category = template.category
        result.owner_id = template.owner_id
        result.service_id = template.service_id

    # 4) Campos (la regex de la plantilla tiene prioridad sobre la regla genérica).
    base = builtin_rules(template, list(templates))

    def rule(name: str) -> str:
        return getattr(template, name, "") or getattr(base, name, "")

    tmpl_amount = _regex_value(rule("amount_regex"), text)
    if tmpl_amount:
        try:
            result.amount = parse_money(tmpl_amount)
        except ValueError:
            result.warnings.append("La regex de importe de la plantilla no devolvió un número.")
    elif result.amount is None:
        result.amount = _after_keyword(lines, folded, AMOUNT_KEYWORDS, _first_money,
                                       exclude=["subtotal", "anterior"])
        if result.amount is None:
            amounts = [parse_money(m.group(1)) for m in MONEY_RE.finditer(text) if "$" in m.group(0)]
            result.amount = max(amounts) if amounts else None

    tmpl_issue = find_dates(_regex_value(rule("issue_regex"), text) or "")
    if tmpl_issue:
        result.issue_date = tmpl_issue[0]

    tmpl_due = _regex_value(rule("due_regex"), text)
    due_candidates = find_dates(tmpl_due) if tmpl_due else []
    if not due_candidates:
        for i, fl in enumerate(folded):
            if any(k in fl for k in DUE_KEYWORDS) and not any(ex in fl for ex in DUE_EXCLUDE):
                found = find_dates(lines[i])
                if not found and i + 1 < len(lines):
                    found = find_dates(lines[i + 1])
                due_candidates.extend(found)
    if due_candidates:
        if result.issue_date:
            due_candidates = [d for d in due_candidates if d >= result.issue_date] or due_candidates
        result.due_date = min(due_candidates)

    if not result.issue_date:
        result.issue_date = _after_keyword(lines, folded, ISSUE_KEYWORDS,
                                           lambda s: (find_dates(s) or [None])[0], lookahead=1)

    if not result.number:
        m = NUMBER_RE.search(text) or LSP_NUMBER_RE.search(fold(text))
        if m:
            result.number = f"{int(m.group(1)):05d}-{m.group(2)}"

    result.account_number = (
        _regex_value(rule("account_regex"), text)
        or _after_keyword(lines, folded, ACCOUNT_KEYWORDS, _account_token, lookahead=0)
        or ""
    )
    result.payment_codes = _regex_codes(rule("code_regex"), text)
    if not result.payment_codes:
        code = _after_keyword(lines, folded, PAYMENT_CODE_KEYWORDS, _digits_token, lookahead=1)
        result.payment_codes = [("", code)] if code else []

    result.period = _find_period(lines, folded)
    if not result.period and result.issue_date:
        result.period = result.issue_date.strftime("%Y-%m")

    result.payment_url = (
        _pick_payment_url(pdf.links + pdf.qr_codes + URL_RE.findall(text))
        or rule("payment_url")
        or next((_normalize_url(q) for q in pdf.qr_codes
                 if q.lower().startswith("http") and "afip.gob.ar" not in q), "")
    )

    # 5) Servicio: por nº de cliente en el texto, o único servicio del proveedor.
    if not result.service_id:
        svc = match_service(text, list(services), result.provider)
        if svc:
            result.service_id = svc.id
            result.owner_id = result.owner_id or svc.owner_id
            result.category = result.category or svc.category
            result.provider = result.provider or svc.provider or svc.name

    if not result.provider and emv.get("59"):
        result.provider = emv["59"].strip().title().replace("S.a.", "S.A.")
    if not result.provider:
        result.provider = _guess_provider_name(lines)
        if result.provider:
            result.warnings.append("No hay plantilla para este proveedor: revisá el nombre.")
    if not text.strip():
        result.warnings.append("El PDF no tiene texto (¿es una foto escaneada?). Sólo se leyó el QR.")
    if result.amount is None:
        result.warnings.append("No se encontró el importe.")
    if result.due_date is None:
        result.warnings.append("No se encontró la fecha de vencimiento.")
    return result
