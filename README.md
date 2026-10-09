# 🏠 House ERP

ERP hogareño para llevar **el stock de la casa** y **las facturas/servicios** (propios o de
terceros), con lectura de códigos de barras, **importación automática de facturas en PDF** y
lectura del QR de las facturas electrónicas de ARCA/AFIP.

Pensado para el celular: barra de navegación inferior, listas táctiles y se instala como app
(PWA). En Android se puede **compartir un PDF desde WhatsApp/Gmail directo a la app**.

Hecho con Python + FastAPI + SQLite. No necesita servidor de base de datos: todo queda en
`data/house.db` (y los adjuntos en `data/uploads/`).

## Arranque rápido

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.demo            # opcional: carga datos de ejemplo
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abrí <http://localhost:8000>. Para usarlo desde el celular en la red de la casa, entrá a
`http://<ip-de-la-pc>:8000`.

> **Cámara:** los navegadores sólo dejan usar la cámara en HTTPS o `localhost`. Para escanear
> desde el celular, poné la app detrás de HTTPS (por ejemplo con Caddy, un túnel de
> Cloudflare o Tailscale Serve). Un lector de códigos USB/Bluetooth funciona siempre,
> porque escribe el código como si fuera un teclado.

Tests: `pytest`.

## Qué hace

### Stock
- **Productos** con código de barras, marca, categoría, presentación, ubicación
  (alacena, heladera, freezer…), unidad y **stock mínimo**.
- **Movimientos**: entradas (con fecha de vencimiento y precio), consumos y ajustes por
  conteo. El stock es la suma de los movimientos, así queda el historial completo.
- **Escáner** (`/stock/escanear`): con la cámara, un lector USB o tipeando.
  - Si el producto ya existe, muestra el stock con botones ±1; también hay un modo
    "sumar 1 por cada escaneo" para cuando guardás la compra del súper.
  - Si no existe, busca el código en APIs públicas y gratuitas, en este orden:
    [Open Food Facts](https://world.openfoodfacts.org) → Open Beauty Facts →
    Open Products Facts → [UPCitemdb](https://www.upcitemdb.com) (100 consultas/día),
    y precarga nombre, marca, categoría, presentación y foto.
- **Lista de compras** automática con lo que está por debajo del mínimo.
- **Alertas de vencimiento** (próximos 7 días).

### Importar facturas en PDF (`/facturas/importar`)
Subís el PDF que te mandan (o lo compartís desde WhatsApp) y se lee solo:

| Dato | De dónde sale |
|---|---|
| CUIT, nº de comprobante, fecha, importe, moneda | QR de ARCA/AFIP impreso en la factura (exacto) |
| Proveedor y categoría | Plantilla que coincide por CUIT o palabras clave |
| Importe (si no hay QR) | "Total a pagar", "Importe a pagar", "Total a abonar"… |
| Vencimiento | 1er vencimiento (ignora "próximo vencimiento" y "vto. CAE") |
| Período, nº de cliente, código de pago electrónico | Texto de la factura |
| Link de pago | Botones/links del PDF, QR o URLs del texto con "pago" |
| Servicio y titular | Servicio cuyo nº de cliente aparece en la factura, o la plantilla |

- Con **un PDF** te muestra la factura precargada para revisar; con **varios**, carga
  directamente las que se leyeron completas y te avisa cuáles faltan.
- Detecta **duplicados** (mismo CUIT + número, o mismo proveedor + importe + vencimiento).
- El PDF queda adjunto y la factura muestra **"Pagar online"** y los códigos de pago para copiar
  (puede tener varios, ej. uno para Banelco y otro para Link).
- **Plantillas** (`/plantillas`): vienen incluidas Edenor, Edesur, Edelap, EPEC, AySA, ABSA,
  Metrogas, Naturgy, Camuzzi, Ecogas, Telecentro, Personal/Fibertel, Movistar, Claro,
  DirecTV, ABL (AGIP), ARBA y expensas. Al guardar una factura importada con
  "Recordar este proveedor", se crea una plantilla por CUIT con el titular, categoría y
  servicio que elegiste: la próxima factura de ese proveedor ya viene asignada (ideal para
  las facturas que mandan las dueñas). Si algún proveedor tiene un formato raro, la
  plantilla admite expresiones regulares propias para importe, vencimiento, emisión, nº de
  cliente y códigos de pago (Naturgy ya las trae, porque en su PDF los valores vienen lejos
  de las etiquetas, y guarda un código para Banelco y otro para Link);
  en la pantalla de revisión podés ver el texto leído del PDF para armarlas.
- Limitación: un PDF escaneado (foto) no tiene texto; de ese se lee sólo el QR.

### Facturas y servicios
- **Facturas** con proveedor, CUIT, número, concepto, categoría, período, importe, moneda
  (ARS/USD), emisión, vencimiento y **adjunto** (PDF o foto).
- **Titular** de cada factura: la Casa, un integrante o un **tercero** (ej. la factura de
  luz de la abuela que pagás vos).
- **Pagos parciales o totales**, con medio de pago y **quién pagó**. Estado automático:
  pendiente / parcial / vencida / pagada.
- **Lectura del QR de ARCA/AFIP**: escaneás el QR de la factura electrónica y se
  completan fecha, CUIT, número de comprobante, importe y moneda. Si ya cargaste facturas
  de ese CUIT, también recuerda el proveedor, la categoría y el titular.
- **Servicios recurrentes** (luz, gas, internet, expensas, prepaga…) con nº de cliente,
  frecuencia, día de vencimiento y monto estimado. El tablero avisa qué servicio todavía no
  tiene la factura del período.
- **Cuentas entre personas**: si alguien paga una factura que no es suya, queda registrada
  la deuda; se puede saldar total o parcialmente.

### Tablero y exportación
- Tablero con facturas vencidas y por vencer, servicios sin factura, lista de compras,
  vencimientos, gasto pagado del mes por categoría y saldos entre personas.
- Exportación a CSV de stock y facturas (abre directo en Excel / Google Sheets).

## Estructura

```
app/
  main.py            # app FastAPI
  models.py          # tablas (SQLModel)
  db.py              # conexión SQLite y datos iniciales
  demo.py            # datos de ejemplo
  routers/           # stock, facturas/servicios, tablero/personas/API/exportación
  services/
    barcode.py       # búsqueda de códigos en APIs públicas
    afip_qr.py       # lectura del QR de facturas electrónicas
    invoice_pdf.py   # importación de facturas PDF + plantillas por proveedor
    links.py         # links y códigos sueltos de cualquier registro (tabla comodín `link`)
    reports.py       # alertas, gastos y saldos
  templates/         # HTML (Jinja2 + Pico CSS)
  static/            # CSS y JS de los escáneres
tests/
```

Variables de entorno opcionales: `HOUSE_ERP_DATA` (carpeta de datos) y `HOUSE_ERP_DB`
(URL de SQLAlchemy, por si algún día querés usar PostgreSQL).

## Ideas para seguir

- **Usuarios y login** (hoy no tiene autenticación: usarlo sólo en la red de la casa o
  detrás de un proxy con login).
- **Recordatorios** de vencimientos por Telegram, WhatsApp o mail.
- **Leer facturas que llegan por mail** (Gmail) y pasarlas por el importador de PDF.
- **OCR** para facturas escaneadas o fotos.
- **Débitos automáticos / Mercado Pago**: importar movimientos para marcar pagos solos.
- **Presupuesto mensual** por categoría y gráficos de evolución de cada servicio.
- **Historial de precios** por producto y comparación entre supermercados.
- **Recetas y menú semanal** que descuenten stock y armen la lista de compras.
- **Inventario de bienes**: electrodomésticos con garantía, manuales y service.
- **Mantenimiento de la casa**: tareas periódicas (filtros, matafuegos, termotanque).
- **Vehículos**: patente, seguro, VTV, service y combustible.
- **Salud y mascotas**: vacunas, turnos, medicamentos (con su stock y vencimiento).
- **Documentos**: escrituras, contratos de alquiler, pólizas, con fecha de renovación.
- **Cotización del dólar** automática para ver todo en una sola moneda.
