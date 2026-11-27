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

// Confirmaciones: modal propio (el confirm() del navegador traba la pestaña).
function confirmDialog(text, okLabel = "Confirmar") {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "cal-dialog";
    dialog.setAttribute("aria-labelledby", "confirm-title");
    dialog.innerHTML = `<h2 id="confirm-title"></h2><div class="cal-dialog-actions">
      <button class="btn btn-red" type="button" data-ok></button>
      <button class="btn" type="button" data-cancel>Cancelar</button></div>`;
    dialog.querySelector("h2").textContent = text;
    dialog.querySelector("[data-ok]").textContent = okLabel;
    document.body.appendChild(dialog);
    const done = (value) => { dialog.close(); dialog.remove(); resolve(value); };
    dialog.querySelector("[data-ok]").addEventListener("click", () => done(true));
    dialog.querySelector("[data-cancel]").addEventListener("click", () => done(false));
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); done(false); }); // Escape
    dialog.showModal();
    dialog.querySelector("[data-cancel]").focus(); // lo seguro queda a mano
  });
}
document.querySelectorAll("[data-confirm]").forEach((form) => {
  form.addEventListener("submit", async (e) => {
    if (form.dataset.confirmed) return;
    e.preventDefault();
    if (await confirmDialog(form.dataset.confirm, form.dataset.confirmOk || "Confirmar")) {
      form.dataset.confirmed = "1";
      form.requestSubmit(e.submitter || undefined);
    }
  });
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

// WhatsApp: al abrir el chat se registra el contacto (y el seguimiento a 3 días) sin otro clic.
document.querySelectorAll("[data-register]").forEach((link) => {
  link.addEventListener("click", () => {
    const text = document.querySelector("[data-message] textarea")?.value || "";
    const body = new URLSearchParams({ channel: link.dataset.channel || "whatsapp", message: text, template: link.dataset.template || "" });
    fetch(link.dataset.register, { method: "POST", body, keepalive: true, headers: { "X-Requested-With": "fetch" } })
      .then((r) => { if (r.ok) { toast("Contacto registrado · seguimiento en 3 días"); setTimeout(() => location.reload(), 1500); } })
      .catch(() => {});
  });
});

// Botones que tardan (actualizar, analizar): avisan que están trabajando y evitan doble clic.
document.addEventListener("submit", (e) => {
  if (e.defaultPrevented) return;
  const button = e.target.querySelector("button[data-busy]");
  if (!button) return;
  button.textContent = button.dataset.busy;
  setTimeout(() => { button.disabled = true; }, 0);
});
