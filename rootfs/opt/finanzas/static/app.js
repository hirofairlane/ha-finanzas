/* HA Finanzas — minimal vanilla-JS front-end.
   Talks to /api/*. No frameworks by design: this ships as a single
   HA add-on with no build step, matching pool-brain's style. */

const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
};

const fmt = (n) => (n == null ? "—" :
  new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" }).format(n));

/* ---------- Tab switcher ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const target = document.getElementById(`tab-${btn.dataset.tab}`);
    target.classList.add("active");
    loaders[btn.dataset.tab]?.();
  });
});

/* ---------- Dashboard ---------- */
async function loadDashboard() {
  const data = await api("api/summary");
  document.getElementById("total-balance").textContent = fmt(data.total_balance);

  const wrap = document.getElementById("months-chart");
  wrap.innerHTML = "";
  for (const m of data.months.slice().reverse()) {
    const card = document.createElement("div");
    card.className = "month-card";
    const netCls = m.net >= 0 ? "pos" : "neg";
    card.innerHTML = `
      <div class="m-label">${m.month}</div>
      <div class="m-net ${netCls}">${fmt(m.net)}</div>
      <div class="m-detail">+${fmt(m.income)} · -${fmt(m.expense)}</div>
      <div class="m-detail">${m.n} mov.</div>`;
    wrap.appendChild(card);
  }

  const strip = document.getElementById("accounts-strip");
  strip.innerHTML = "";
  for (const a of data.accounts) {
    const el = document.createElement("div");
    el.className = "account-card";
    el.innerHTML = `
      <div class="a-bank">${a.bank}</div>
      <div class="a-alias">${a.alias || "(sin alias)"}</div>
      <div class="a-iban">${a.iban}</div>
      <div class="a-balance">${fmt(a.last_balance)}</div>
      <div class="hint">${a.n_tx} movimientos</div>`;
    strip.appendChild(el);
  }
}

/* ---------- Accounts detail ---------- */
async function loadAccounts() {
  const data = await api("api/summary");
  const sel = document.getElementById("accounts-select");
  sel.innerHTML = "";
  const select = document.createElement("select");
  select.innerHTML = data.accounts.map((a) =>
    `<option value="${a.id}">${a.bank} — ${a.alias || a.iban}</option>`).join("");
  sel.appendChild(select);

  const render = async () => {
    const acc = data.accounts.find((a) => a.id == select.value);
    const { transactions } = await api(`api/transactions?account=${select.value}&transfers=1&limit=200`);
    const box = document.getElementById("account-detail");
    box.innerHTML = `
      <div class="row" style="margin-top:12px">
        <div><b>Saldo:</b> ${fmt(acc.last_balance)}</div>
        <div><b>Movimientos:</b> ${acc.n_tx}</div>
      </div>
      ${renderTxTable(transactions)}`;
  };
  select.addEventListener("change", render);
  await render();
}

function renderTxTable(rows) {
  if (!rows.length) return "<p class='hint'>Sin movimientos.</p>";
  const body = rows.map((t) => {
    const cls = t.amount < 0 ? "neg" : "pos";
    const trCls = t.is_transfer ? "is-transfer" : "";
    const cat = t.category_name
      ? `<span class="pill" style="background:${t.category_color||'#fff5d6'}">${t.category_name}</span>`
      : "";
    const merch = t.merchant_name ? ` <b>${t.merchant_name}</b>` : "";
    const xfer = t.is_transfer ? ` <span class="pill">↔ traspaso</span>` : "";
    return `<tr class="${trCls}">
      <td>${t.op_date}</td>
      <td>${cat}${merch}${xfer}<div class="hint">${t.concept}</div></td>
      <td class="num ${cls}">${fmt(t.amount)}</td>
      <td class="num">${fmt(t.balance)}</td>
    </tr>`;
  }).join("");
  return `<table class="tx-table">
    <thead><tr><th>Fecha</th><th>Concepto</th><th>Importe</th><th>Saldo</th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

/* ---------- Month breakdown ---------- */
let _allCategories = [];      // cached for the inline picker
async function ensureCategories() {
  if (!_allCategories.length) {
    const { categories } = await api("api/categories");
    _allCategories = categories;
  }
  return _allCategories;
}
function invalidateCategories() { _allCategories = []; }

async function loadMonth() {
  const picker = document.getElementById("month-picker");
  if (!picker.value) {
    const d = new Date();
    picker.value = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
  }

  const render = async (highlightCatId = null) => {
    await ensureCategories();
    const { categories, top_merchants } = await api(`api/month?month=${picker.value}`);
    const box = document.getElementById("month-breakdown");
    if (!categories.length) {
      box.innerHTML = "<p class='hint'>Sin datos.</p>";
      document.getElementById("month-txs").innerHTML = "";
      return;
    }
    const max = Math.max(...categories.map(c => Math.abs(c.total || 0))) || 1;
    box.innerHTML = categories.map((c) => {
      const pct = Math.round((Math.abs(c.total||0) / max) * 100);
      const color = c.color || "#bdbdbd";
      const kind = c.kind || "gasto";
      const isSel = highlightCatId != null && c.id == highlightCatId;
      return `<div class="provision-row clickable ${isSel ? 'selected' : ''}"
                   data-cat-id="${c.id ?? ''}" data-cat-kind="${kind}">
        <span class="cat-swatch" style="background:${color}"></span>
        <div>
          <div class="p-name">
            <span class="cat-kind ${kind}">${kind}</span>
            ${c.name || "(sin categoría)"}
          </div>
          <div class="bar"><div style="background:${color};width:${pct}%"></div></div>
        </div>
        <div class="p-amount">${fmt(c.total)}</div>
        <div class="hint">${c.n} mov.</div>
      </div>`;
    }).join("");

    box.querySelectorAll(".provision-row.clickable").forEach((row) => {
      row.onclick = () => {
        const cid = row.dataset.catId;
        loadMonthTxs(picker.value, cid || null);
        // re-highlight without a full reload
        box.querySelectorAll(".provision-row").forEach(r => r.classList.remove("selected"));
        row.classList.add("selected");
      };
    });

    const mbox = document.getElementById("month-merchants");
    mbox.innerHTML = top_merchants.length
      ? top_merchants.map((m) => `<div class="provision-row">
          <span class="cat-swatch" style="background:#ff6f3c"></span>
          <div class="p-name">${m.name}</div>
          <div class="p-amount">${fmt(m.total)}</div>
          <div class="hint">${m.n} mov.</div>
        </div>`).join("")
      : "<p class='hint'>Sin datos.</p>";

    // Default: show all txs of the month.
    if (highlightCatId === null) await loadMonthTxs(picker.value, null);
  };

  picker.onchange = () => render(null);
  await render(null);
}

async function loadMonthTxs(month, categoryId) {
  await ensureCategories();
  const params = new URLSearchParams({ month, limit: "500" });
  if (categoryId) params.set("category", categoryId);
  const { transactions } = await api(`api/transactions?${params}`);
  const box = document.getElementById("month-txs");
  const title = categoryId
    ? `Movimientos filtrados (${transactions.length})`
    : `Movimientos del mes (${transactions.length})`;
  box.innerHTML = `<h3>${title}</h3>${renderTxTableEditable(transactions)}`;
  wireInlineCategorise(box);
}

function renderTxTableEditable(rows) {
  if (!rows.length) return "<p class='hint'>Sin movimientos.</p>";
  const body = rows.map((t) => {
    const cls = t.amount < 0 ? "neg" : "pos";
    const trCls = t.is_transfer ? "is-transfer" : "";
    const kind = t.amount > 0 ? "ingreso" : "gasto";
    const catCell = t.category_name
      ? `<span class="pill" style="background:${t.category_color||'#fff5d6'}">${t.category_name}</span>`
      : `<span class="pill" style="background:#eee">sin cat.</span>`;
    const merch = t.merchant_name ? ` <b>${t.merchant_name}</b>` : "";
    const xfer = t.is_transfer ? ` <span class="pill">↔ traspaso</span>` : "";
    return `<tr class="${trCls}" data-tx-id="${t.id}" data-merchant-id="${t.merchant_id||''}" data-kind="${kind}">
      <td>${t.op_date}</td>
      <td>
        <div>${catCell}${merch}${xfer}</div>
        <div class="hint">${t.concept}</div>
        <div class="inline-cat" hidden></div>
      </td>
      <td class="num ${cls}">${fmt(t.amount)}</td>
      <td class="num">${fmt(t.balance)}</td>
      <td><button class="btn small edit-cat">Categoría</button></td>
    </tr>`;
  }).join("");
  return `<table class="tx-table">
    <thead><tr><th>Fecha</th><th>Concepto</th><th>Importe</th><th>Saldo</th><th></th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

function wireInlineCategorise(scope) {
  scope.querySelectorAll("button.edit-cat").forEach((btn) => {
    btn.onclick = () => {
      const tr = btn.closest("tr");
      const kind = tr.dataset.kind;
      const merchantId = tr.dataset.merchantId || null;
      const box = tr.querySelector(".inline-cat");
      const opts = _allCategories
        .filter((c) => c.kind === kind)
        .map((c) => `<option value="${c.id}">${c.name}</option>`).join("");
      const cascade = merchantId
        ? `<label><input type="checkbox" class="cascade" checked /> aplicar a todos los movimientos de este comercio</label>`
        : `<span class="hint">sin comercio detectado — sólo esta fila</span>`;
      box.innerHTML = `
        <div class="row">
          <select class="cat-select">${opts}</select>
          <button class="btn small primary save">Guardar</button>
          <button class="btn small cancel">Cancelar</button>
        </div>
        <div>${cascade}</div>`;
      box.hidden = false;

      box.querySelector(".cancel").onclick = () => { box.hidden = true; box.innerHTML = ""; };
      box.querySelector(".save").onclick = async () => {
        const catId = parseInt(box.querySelector(".cat-select").value, 10);
        const cascadeChecked = box.querySelector(".cascade")?.checked;
        if (merchantId && cascadeChecked) {
          await fetch(`api/merchants/${merchantId}/categorise`, {
            method: "POST", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({ category_id: catId }),
          });
        } else {
          await fetch(`api/transactions/${tr.dataset.txId}/categorise`, {
            method: "POST", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({ category_id: catId, merchant_id: merchantId ? parseInt(merchantId,10) : null }),
          });
        }
        // Reload month view to reflect the change.
        await loadMonth();
      };
    };
  });
}

/* ---------- Provisions ---------- */
async function loadProvisions() {
  const { provisions } = await api("api/provisions");
  const box = document.getElementById("provisions-list");
  if (!provisions.length) { box.innerHTML = "<p class='hint'>Sin histórico suficiente.</p>"; return; }
  box.innerHTML = provisions.map((p) => `
    <div class="provision-row">
      <span class="cat-swatch" style="background:${p.color || '#ff6f3c'}"></span>
      <div>
        <div class="p-name">${p.name || "(sin categoría)"}</div>
        <div class="hint">${p.kind || ''}</div>
      </div>
      <div>
        <div class="p-amount p-6m">${fmt(p.avg_6m)} <span class="hint">/6m</span></div>
        <div class="p-12m">${fmt(p.avg_12m)} /12m</div>
      </div>
    </div>`).join("");
}

/* ---------- Categories CRUD ---------- */
async function loadCategories() {
  const { categories } = await api("api/categories");
  const box = document.getElementById("categories-list");
  box.className = "cat-list";
  box.innerHTML = categories.map((c) => `
    <div class="cat-row" data-id="${c.id}">
      <span class="cat-swatch" style="background:${c.color}"></span>
      <span class="cat-kind ${c.kind}">${c.kind}</span>
      <span class="cat-name">${c.name}</span>
      <span class="cat-actions">
        <button class="btn small edit">Editar</button>
        <button class="btn small danger delete">Eliminar</button>
      </span>
    </div>`).join("");

  box.querySelectorAll(".delete").forEach((b) => b.onclick = async (e) => {
    const id = e.target.closest(".cat-row").dataset.id;
    if (!confirm("¿Eliminar categoría?")) return;
    await api(`api/categories/${id}`, { method: "DELETE" });
    invalidateCategories(); loadCategories();
  });
  box.querySelectorAll(".edit").forEach((b) => b.onclick = async (e) => {
    const row = e.target.closest(".cat-row");
    const id = row.dataset.id;
    const cur = categories.find((c) => c.id == id);
    const name = prompt("Nombre:", cur.name); if (!name) return;
    const color = prompt("Color hex:", cur.color) || cur.color;
    await api("api/categories", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ ...cur, name, color }),
    });
    invalidateCategories(); loadCategories();
  });
}

document.getElementById("new-cat").onclick = async () => {
  const kind = prompt("Tipo (gasto/ingreso):", "gasto");
  if (!["gasto","ingreso"].includes(kind)) return;
  const name = prompt("Nombre:"); if (!name) return;
  const color = prompt("Color hex:", "#ff6f3c") || "#ff6f3c";
  await api("api/categories", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ kind, name, color, icon: "mdi:tag" }),
  });
  loadCategories();
};

/* ---------- Upload ---------- */
document.getElementById("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = document.getElementById("upload-file").files[0];
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  const out = document.getElementById("upload-result");
  out.textContent = "Ingestando…";
  try {
    const res = await fetch("api/upload", { method: "POST", body: fd });
    const j = await res.json();
    out.textContent = JSON.stringify(j, null, 2);
    loadDashboard();
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

const loaders = {
  dashboard: loadDashboard,
  accounts: loadAccounts,
  month: loadMonth,
  provisions: loadProvisions,
  categories: loadCategories,
  import: () => {},
};

loadDashboard().catch((e) => console.error(e));
