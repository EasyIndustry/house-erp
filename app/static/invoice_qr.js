// Precarga de facturas a partir del QR de ARCA/AFIP.
const form = document.querySelector("form[enctype]");
const msg = document.getElementById("qr-msg");
const paidNow = document.getElementById("paid_now");
paidNow.addEventListener("change", () => document.getElementById("paid-fields").hidden = !paidNow.checked);

function setField(name, value) {
  if (value === undefined || value === null || value === "") return;
  const field = form.elements[name];
  if (field) field.value = value;
}

async function parseQr(text) {
  msg.textContent = "Leyendo…";
  const r = await fetch("/api/afip-qr", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const data = await r.json();
  if (!r.ok) { msg.textContent = "⚠️ " + (data.detail || "QR inválido"); return; }
  ["provider", "provider_cuit", "number", "issue_date", "amount", "currency",
   "category", "owner_id", "service_id"].forEach((k) => setField(k, data[k]));
  if (data.issue_date) setField("period", data.issue_date.slice(0, 7));
  msg.textContent = `✅ ${data.comprobante} ${data.number} · CAE ${data.cae}` +
    (data.provider ? "" : " · completá el nombre del proveedor (es la primera factura de ese CUIT)");
}

document.getElementById("qr-parse").addEventListener("click", () => parseQr(document.getElementById("qr-text").value));

let scanner = null;
document.getElementById("qr-cam").addEventListener("click", async () => {
  if (scanner) { await scanner.stop(); scanner = null; return; }
  if (typeof Html5Qrcode === "undefined") { msg.textContent = "No se pudo cargar el lector de cámara."; return; }
  scanner = new Html5Qrcode("reader");
  try {
    await scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 250 }, async (text) => {
      await scanner.stop(); scanner = null;
      document.getElementById("qr-text").value = text;
      parseQr(text);
    });
  } catch (e) {
    scanner = null;
    msg.textContent = "No se pudo abrir la cámara (requiere HTTPS o localhost).";
  }
});
