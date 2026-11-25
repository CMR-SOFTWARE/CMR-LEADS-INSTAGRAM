// Programar en Instagram: los archivos van directo del navegador a Supabase Storage.
// Las fotos se pasan a JPG (lo único que Meta acepta por link) con 1440 px de ancho como máximo.
(function () {
  const form = document.getElementById("form-programar");
  if (!form) return;
  const input = document.getElementById("archivos");
  const hidden = document.getElementById("archivos-subidos");
  const errorBox = document.getElementById("subida-error");
  const status = document.getElementById("subida-estado");
  const bar = document.getElementById("subida-avance");
  const button = document.getElementById("btn-programar");
  const MAX_BYTES = 50 * 1024 * 1024;
  const MAX_WIDTH = 1440;
  let sending = false;

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = false;
    status.hidden = true;
    bar.hidden = true;
    button.disabled = false;
    sending = false;
  }

  function isVideo(file) {
    return /\.(mp4|mov)$/i.test(file.name) || file.type.startsWith("video/");
  }

  function toJpeg(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, MAX_WIDTH / img.naturalWidth);
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff"; // las transparencias de PNG quedan en blanco
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("jpg"))), "image/jpeg", 0.92);
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("No se pudo leer " + file.name + ". Probá con otra foto (JPG o PNG)."));
      };
      img.src = url;
    });
  }

  function upload(url, blob, name, contentType, onProgress) {
    return new Promise((resolve, reject) => {
      const data = new FormData();
      data.append("cacheControl", "3600");
      data.append("", new File([blob], name, { type: contentType }));
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.setRequestHeader("x-upsert", "true");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () =>
        xhr.status < 300 ? resolve() : reject(new Error("Supabase rechazó " + name + " (" + xhr.status + ")."));
      xhr.onerror = () => reject(new Error("Se cortó la subida de " + name + ". Revisá la conexión."));
      xhr.send(data);
    });
  }

  form.addEventListener("submit", async (event) => {
    if (hidden.value) return; // ya subido: se envía el formulario
    event.preventDefault();
    if (sending) return;
    errorBox.hidden = true;
    const files = Array.from(input.files || []);
    if (!files.length) return showError("Elegí al menos un archivo.");
    const heavy = files.find((f) => isVideo(f) && f.size > MAX_BYTES);
    if (heavy) {
      return showError(heavy.name + " pesa más de 50 MB. Exportalo más liviano (1080p alcanza para Instagram).");
    }
    sending = true;
    button.disabled = true;
    status.hidden = false;
    bar.hidden = false;
    bar.value = 0;
    try {
      status.textContent = "Preparando…";
      const prepared = [];
      for (const file of files) {
        if (isVideo(file)) {
          const type = /\.mov$/i.test(file.name) ? "video/quicktime" : "video/mp4";
          prepared.push({ name: file.name, blob: file, type: type });
        } else {
          prepared.push({ name: file.name.replace(/\.[^.]+$/, "") + ".jpg", blob: await toJpeg(file), type: "image/jpeg" });
        }
      }
      const response = await fetch("/instagram/subidas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ formato: form.dataset.formato, nombres: prepared.map((p) => p.name) }),
      });
      const answer = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(answer.error || "No se pudo preparar la subida.");
      const total = prepared.reduce((sum, p) => sum + p.blob.size, 0) || 1;
      let done = 0;
      for (let i = 0; i < prepared.length; i++) {
        const p = prepared[i];
        status.textContent = "Subiendo " + (i + 1) + " de " + prepared.length + "…";
        await upload(answer.archivos[i].url, p.blob, p.name, p.type, (part) => {
          bar.value = Math.round(((done + part * p.blob.size) / total) * 100);
        });
        done += p.blob.size;
      }
      bar.value = 100;
      status.textContent = "Guardando…";
      hidden.value = JSON.stringify(answer.archivos.map((a) => a.key));
      form.submit();
    } catch (err) {
      showError(err.message || "No se pudo subir. Probá de nuevo.");
    }
  });
})();
