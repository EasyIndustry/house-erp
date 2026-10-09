// Escáner de stock: cámara (html5-qrcode) o lector USB / teclado.
const result = document.getElementById("result");
const codeInput = document.getElementById("code");
const mode = document.getElementById("mode");
let scanner = null;
let lastCode = null;
let lastTime = 0;

try { mode.value = localStorage.getItem("scanMode") || "show"; } catch (e) {}
mode.addEventListener("change", () => { try { localStorage.setItem("scanMode", mode.value); } catch (e) {} });

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (text) node.textContent = text;
  return node;
}

async function adjust(productId, delta, stockNode) {
  const r = await fetch(`/api/stock/${productId}/ajustar`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ delta }),
  });
  const data = await r.json();
  stockNode.textContent = data.stock;
}

function showExisting(p) {
  result.replaceChildren();
  if (p.image_url) result.append(el("img", { src: p.image_url, alt: "" }));
  result.append(el("h3", {}, p.name));
  if (p.brand) result.append(el("p", { class: "muted" }, p.brand));
  const stockP = el("p", {}, "Stock: ");
  const stockNode = el("strong", {}, String(p.stock));
  stockP.append(stockNode, ` ${p.unit}`);
  result.append(stockP);
  const actions = el("div", { class: "actions" });
  const minus = el("button", { class: "secondary" }, "− 1");
  const plus = el("button", {}, "+ 1");
  minus.onclick = () => adjust(p.id, -1, stockNode);
  plus.onclick = () => adjust(p.id, 1, stockNode);
  actions.append(minus, plus, el("a", { href: `/stock/${p.id}`, role: "button", class: "outline" }, "Ver detalle"));
  result.append(actions);
  result.hidden = false;
  if (mode.value !== "show") adjust(p.id, Number(mode.value), stockNode);
}

function showNew(code, data) {
  result.replaceChildren();
  if (data.found) {
    const info = data.info;
    if (info.image_url) result.append(el("img", { src: info.image_url, alt: "" }));
    result.append(el("h3", {}, info.name));
    result.append(el("p", { class: "muted" }, `${info.brand} ${info.package_size} · fuente: ${info.source}`));
  } else {
    result.append(el("h3", {}, `Código ${code}`));
    result.append(el("p", { class: "muted" }, "No está en las bases públicas; vas a tener que cargarlo a mano."));
  }
  result.append(el("a", { href: `/stock/nuevo?barcode=${encodeURIComponent(code)}`, role: "button" }, "Agregar al inventario"));
  result.hidden = false;
}

async function lookup(code) {
  code = code.replace(/\D/g, "");
  if (!code) return;
  result.hidden = false;
  result.replaceChildren(el("p", { "aria-busy": "true" }, "Buscando…"));
  try {
    const r = await fetch(`/api/barcode/${code}`);
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    data.exists ? showExisting(data.product) : showNew(code, data);
  } catch (e) {
    result.replaceChildren(el("p", {}, "Error buscando el código: " + e.message));
  }
}

document.getElementById("manual").addEventListener("submit", (ev) => {
  ev.preventDefault();
  lookup(codeInput.value);
  codeInput.value = "";
  codeInput.focus();
});

document.getElementById("cam-toggle").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  if (scanner) {
    await scanner.stop();
    scanner = null;
    btn.textContent = "📷 Activar cámara";
    return;
  }
  if (typeof Html5Qrcode === "undefined") {
    alert("No se pudo cargar el lector de cámara (¿sin internet?).");
    return;
  }
  scanner = new Html5Qrcode("reader");
  btn.textContent = "Detener cámara";
  try {
    await scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 280, height: 160 } }, (text) => {
      // Evita contar dos veces el mismo código mientras sigue frente a la cámara.
      const now = Date.now();
      if (text === lastCode && now - lastTime < 2500) return;
      lastCode = text; lastTime = now;
      lookup(text);
    });
  } catch (e) {
    scanner = null;
    btn.textContent = "📷 Activar cámara";
    alert("No se pudo abrir la cámara. Recordá que el navegador sólo la permite en HTTPS o localhost.\n" + e);
  }
});
