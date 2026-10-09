# 🏠 House ERP

ERP hogareño para llevar **el stock de la casa** y **las facturas/servicios** (propios o de
terceros), con lectura de códigos de barras y del QR de las facturas electrónicas de ARCA/AFIP.

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
- **Leer facturas que llegan por mail** (Gmail) y cargarlas solas, extrayendo los datos
  del PDF.
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
- **PWA** para instalarla en el celular y usarla offline.
