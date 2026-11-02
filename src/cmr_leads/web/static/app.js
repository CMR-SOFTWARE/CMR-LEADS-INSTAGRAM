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
  document.body.appendChild(el);
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
