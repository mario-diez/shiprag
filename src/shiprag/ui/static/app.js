async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  return res.json();
}

function pillClass(status) {
  if (status === "ok") return "ok";
  if (status === "conflict") return "conflict";
  if (status === "clarify") return "clarify";
  return "abstain";
}

async function init() {
  try {
    const h = await api("/api/health");
    const el = document.getElementById("health");
    const rt = h.runtime || {};
    el.textContent = `OK · ${h.zones_indexed.length} zonas · ${h.embedder}`;
    el.classList.add("ok");
    el.title = JSON.stringify(rt, null, 2);
    const badge = document.getElementById("profile-badge");
    badge.textContent = `perfil ${rt.profile || "?"}`;
    badge.title = rt.profile_label || "";
    const zones = await api("/api/zones");
    for (const sel of ["zone", "ingest-zone"]) {
      const node = document.getElementById(sel);
      for (const z of zones) {
        const opt = document.createElement("option");
        opt.value = z.id;
        opt.textContent = z.label;
        node.appendChild(opt);
      }
    }
  } catch (e) {
    document.getElementById("health").textContent = "API no disponible";
  }
}

document.getElementById("examples").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".chip");
  if (!btn) return;
  document.getElementById("query").value = btn.dataset.q || "";
  document.getElementById("emergency").checked = btn.dataset.em === "1";
  if (btn.dataset.em === "1") {
    document.getElementById("mode").value = "extractive";
  }
});

document.getElementById("btn-emergency").addEventListener("click", () => {
  document.getElementById("emergency").checked = true;
  document.getElementById("mode").value = "citations_only";
  document.getElementById("query-form").requestSubmit();
});

document.getElementById("query-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const query = document.getElementById("query").value.trim();
  if (!query) return;
  const body = {
    query,
    zone: document.getElementById("zone").value || null,
    mode: document.getElementById("mode").value,
    emergency: document.getElementById("emergency").checked,
  };
  const box = document.getElementById("answer-box");
  box.hidden = false;
  document.getElementById("answer").textContent = "Consultando índice local…";
  try {
    const data = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const pill = document.getElementById("status-pill");
    pill.textContent = data.status;
    pill.className = "pill " + pillClass(data.status);
    document.getElementById("conf").textContent = `confianza ${data.confidence?.toFixed?.(3) ?? data.confidence}`;
    document.getElementById("zones-used").textContent = `zonas: ${(data.zones_used || []).join(", ")} · modo ${data.mode_used}`;
    document.getElementById("answer").textContent = data.answer;
    const clarify = document.getElementById("clarify");
    if (data.clarification_question) {
      clarify.hidden = false;
      clarify.textContent = data.clarification_question;
    } else {
      clarify.hidden = true;
    }
    const cites = document.getElementById("citations");
    if (!data.citations?.length) {
      cites.className = "citations empty";
      cites.textContent = "Sin citas.";
    } else {
      cites.className = "citations";
      cites.innerHTML = data.citations.map((c) => `
        <article class="cite">
          <h3>${escapeHtml(c.title)} <small>v${escapeHtml(c.version)}</small></h3>
          <div class="meta">pág. ${c.page_start}${c.page_end !== c.page_start ? "–" + c.page_end : ""}${c.section ? " · " + escapeHtml(c.section) : ""} · score ${Number(c.score).toFixed(3)}</div>
          <p>${escapeHtml(c.quote)}</p>
        </article>
      `).join("");
    }
    const trace = document.getElementById("trace");
    trace.className = "mini";
    trace.textContent = JSON.stringify(data.trace, null, 2);
  } catch (e) {
    document.getElementById("answer").textContent = "Error: " + e.message;
  }
});

document.getElementById("ingest-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const file = document.getElementById("file").files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const z = document.getElementById("ingest-zone").value;
  if (z) fd.append("zone", z);
  document.getElementById("ingest-result").textContent = "Indexando…";
  try {
    const data = await api("/api/ingest", { method: "POST", body: fd });
    document.getElementById("ingest-result").textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    document.getElementById("ingest-result").textContent = "Error: " + e.message;
  }
});

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

init();
