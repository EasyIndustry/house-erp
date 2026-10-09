// Service worker mínimo: hace la app instalable (y habilita "Compartir → House ERP").
// No cachea nada: los datos siempre se leen del servidor.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
