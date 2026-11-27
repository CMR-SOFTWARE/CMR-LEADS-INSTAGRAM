// Mensaje editable: el link de WhatsApp se arma con el texto actual.
document.querySelectorAll("[data-message]").forEach((box) => {
  const textarea = box.querySelector("textarea");
  const wa = box.querySelector("[data-wa]");
  const copyBtn = box.querySelector("[data-copy]");
  const dmBtn = box.querySelector("[data-dm]");
  const hidden = document.querySelector("[data-sent-message]");
  const sync = () => {
    if (wa) wa.href = `https://wa.me/${wa.dataset.wa}?text=${encodeURIComponent(textarea.value)}`;
    if (hidden) hidden.value = textarea.value;
  };
  textarea.addEventListener("input", sync);
  sync();
  const copy = async () => {
    try { await navigator.clipboard.writeText(textarea.value); } catch { textarea.select(); document.execCommand("copy"); }
    toast("Mensaje copiado");
  };
  copyBtn?.addEventListener("click", copy);
  dmBtn?.addEventListener("click", copy); // copia y abre el DM: solo hay que pegar
});

// Revisión rápida con teclado.
const review = document.querySelector("[data-review]");
if (review) {
  const reasons = review.querySelector(".reasons");
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select")) return;
    const key = e.key.toLowerCase();
    if (key === "a") review.querySelector("[data-approve]")?.click();
    if (key === "s") review.querySelector("[data-skip]")?.click();
    if (key === "d") reasons.classList.toggle("open");
    if (reasons.classList.contains("open") && /^[1-8]$/.test(key)) {
      reasons.querySelectorAll("button")[Number(key) - 1]?.click();
    }
    if (key === "escape") reasons.classList.remove("open");
  });
  review.querySelector("[data-reject]")?.addEventListener("click", () => reasons.classList.toggle("open"));
}

function toast(text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  // Dentro de la región aria-live para que el lector de pantalla lo anuncie.
  (document.querySelector(".toast-region") || document.body).appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

// Confirmaciones simples.
document.querySelectorAll("[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => { if (!confirm(form.dataset.confirm)) e.preventDefault(); });
});

// Filtros del listado: se aplican apenas se elige una opción.
document.querySelectorAll("form.filters select").forEach((select) => {
  select.addEventListener("change", () => select.form.submit());
});

// Búsqueda en Google: los chips suman rubros (por coma) o localidades (una por línea).
document.querySelectorAll("[data-add]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const field = document.querySelector(`[data-terms="${chip.dataset.add}"]`);
    const sep = chip.dataset.add === "zonas" ? "\n" : ", ";
    const current = field.value.split(sep === "\n" ? /\n/ : /,/).map((t) => t.trim()).filter(Boolean);
    if (!current.includes(chip.value)) field.value = [...current, chip.value].join(sep);
    field.focus();
  });
});

// Embudo: el motivo de pérdida aparece (y es obligatorio) solo al elegir "Perdido".
document.querySelectorAll("[data-pipeline-form]").forEach((form) => {
  const stage = form.querySelector("[data-stage-select]");
  const reasonBox = form.querySelector("[data-lost-reason]");
  const reason = reasonBox?.querySelector("select");
  const sync = () => {
    const lost = stage.value === "lost";
    reasonBox.hidden = !lost;
    if (reason) reason.required = lost;
  };
  stage?.addEventListener("change", sync);
  if (stage && reasonBox) sync();
});

// Envíos: abrir WhatsApp o el DM NO registra nada. Al volver a esta pestaña se pregunta
// "¿Lo enviaste?" y recién con "Sí" se registra (el servidor ignora un 2.º registro igual
// en menos de 10 minutos). Lo pendiente se guarda por si la pestaña se recarga.
const CHANNEL_NAMES = { whatsapp: "WhatsApp", instagram_dm: "Instagram" };
const PENDING_KEY = "cmr-envio-pendiente";
const readPending = () => { try { return JSON.parse(sessionStorage.getItem(PENDING_KEY)); } catch { return null; } };
const savePending = (p) => { try { p ? sessionStorage.setItem(PENDING_KEY, JSON.stringify(p)) : sessionStorage.removeItem(PENDING_KEY); } catch {} };
const registerLinks = document.querySelectorAll("[data-register]");
const setBusy = (busy) => registerLinks.forEach((l) => l.classList.toggle("is-busy", busy));

function askSent() {
  const pending = readPending();
  if (!pending || pending.page !== location.pathname || document.querySelector(".sent-dialog")) return;
  const dialog = document.createElement("dialog");
  dialog.className = "cal-dialog sent-dialog";
  dialog.setAttribute("aria-labelledby", "sent-title");
  dialog.innerHTML = `<h2 id="sent-title"></h2><p class="muted small" style="margin:0 0 12px">Se registra solo si lo mandaste.</p>
    <div class="cal-dialog-actions"><button class="btn btn-green" type="button" data-yes>Sí, lo envié</button>
    <button class="btn" type="button" data-no>No lo envié</button></div>`;
  dialog.querySelector("h2").textContent = `¿Lo enviaste por ${CHANNEL_NAMES[pending.channel] || "mensaje"}?`;
  document.body.appendChild(dialog);
  const close = () => { savePending(null); setBusy(false); dialog.close(); dialog.remove(); };
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); }); // hay que elegir Sí o No
  dialog.querySelector("[data-no]").addEventListener("click", () => { close(); toast("No se registró nada"); });
  const yes = dialog.querySelector("[data-yes]");
  yes.addEventListener("click", () => {
    yes.disabled = true;
    yes.textContent = "Registrando…";
    fetch(pending.url, { method: "POST", body: new URLSearchParams(pending.body), headers: { "X-Requested-With": "fetch" } })
      .then(async (r) => {
        if (r.status === 204) { close(); toast("Envío registrado · seguimiento en 3 días"); setTimeout(() => location.reload(), 1500); }
        else if (r.ok) { close(); toast("Ya estaba registrado"); }
        else { yes.disabled = false; yes.textContent = "Sí, lo envié"; toast((await r.text()) || "No se pudo registrar"); }
      })
      .catch(() => { yes.disabled = false; yes.textContent = "Sí, lo envié"; toast("Sin conexión: probá de nuevo"); });
  });
  dialog.showModal();
  yes.focus();
}

registerLinks.forEach((link) => {
  link.addEventListener("click", (e) => {
    if (link.classList.contains("is-busy")) { e.preventDefault(); return; } // doble clic
    const box = link.closest("[data-message]") || document.querySelector("[data-message]");
    const text = box?.querySelector("textarea")?.value || "";
    savePending({
      page: location.pathname,
      url: link.dataset.register,
      channel: link.dataset.channel || "whatsapp",
      body: { channel: link.dataset.channel || "whatsapp", message: text, template: link.dataset.template || "" },
    });
    setBusy(true);
    // Si el navegador no cambió de pestaña (ventana bloqueada, otra pantalla), preguntar igual.
    let left = false;
    const onHide = () => { if (document.hidden) left = true; };
    document.addEventListener("visibilitychange", onHide, { once: true });
    setTimeout(() => { if (!left) askSent(); }, 4000);
  });
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) setTimeout(askSent, 300); });
if (readPending()) askSent();

// Botones que tardan (actualizar, analizar): avisan que están trabajando y evitan doble clic.
document.addEventListener("submit", (e) => {
  if (e.defaultPrevented) return;
  const button = e.target.querySelector("button[data-busy]");
  if (!button) return;
  button.textContent = button.dataset.busy;
  setTimeout(() => { button.disabled = true; }, 0);
});
