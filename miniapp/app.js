(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const el = (id) => document.getElementById(id);

  const DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const MONTHS_RU = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

  let ROLE = null;         // "provider" | "client" | "guest"
  let PROVIDER = null;     // {id, name, category, terms, link} — если роль provider
  let CLIENT_HOME = null;  // ответ /api/client/home — если роль client
  let TAB = null;

  // ---------- утилиты ----------

  function applyTheme() {
    if (!tg || !tg.themeParams) return;
    const tp = tg.themeParams;
    const root = document.documentElement.style;
    const map = {
      bg_color: "--tg-bg", text_color: "--tg-text", hint_color: "--tg-hint",
      link_color: "--tg-link", button_color: "--tg-button", button_text_color: "--tg-button-text",
      secondary_bg_color: "--tg-secondary-bg", destructive_text_color: "--tg-destructive",
    };
    Object.entries(map).forEach(([k, v]) => { if (tp[k]) root.setProperty(v, tp[k]); });
  }

  function fmtDay(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    const wd = (dt.getDay() + 6) % 7;
    return `${DAYS_RU[wd]}, ${d} ${MONTHS_RU[m]}`;
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function showToast(text) {
    const t = el("toast");
    t.textContent = text;
    t.hidden = false;
    setTimeout(() => { t.hidden = true; }, 2500);
  }

  function haptic(kind) {
    if (tg && tg.HapticFeedback) {
      if (kind === "success" || kind === "error" || kind === "warning") {
        tg.HapticFeedback.notificationOccurred(kind);
      } else {
        tg.HapticFeedback.impactOccurred(kind || "light");
      }
    }
  }

  function showConfirm(text, onYes) {
    const overlay = el("confirm-overlay");
    el("confirm-text").textContent = text;
    overlay.hidden = false;
    const yesBtn = el("confirm-yes");
    const noBtn = el("confirm-no");
    const cleanup = () => { overlay.hidden = true; yesBtn.onclick = null; noBtn.onclick = null; };
    yesBtn.onclick = () => { cleanup(); onYes(); };
    noBtn.onclick = cleanup;
  }

  function openSheet(innerHtml) {
    el("sheet-card").innerHTML = innerHtml;
    el("sheet-overlay").hidden = false;
  }

  function closeSheet() {
    el("sheet-overlay").hidden = true;
    el("sheet-card").innerHTML = "";
  }

  el("sheet-overlay").addEventListener("click", (e) => {
    if (e.target.id === "sheet-overlay") closeSheet();
  });

  async function api(path, options) {
    options = options || {};
    const headers = Object.assign(
      { "Content-Type": "application/json", "X-Telegram-Init-Data": (tg && tg.initData) || "" },
      options.headers || {}
    );
    const res = await fetch(path, Object.assign({}, options, { headers }));
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function priceDurationLine(price, duration) {
    const parts = [];
    if (duration) parts.push(`${duration} мин`);
    if (price) parts.push(`${price}₽`);
    return parts.join(" · ");
  }

  // ---------- инициализация / роутинг ----------

  async function init() {
    if (tg) { tg.ready(); tg.expand(); applyTheme(); }

    if (!tg || !tg.initData) {
      el("loading").hidden = true;
      el("auth-error").hidden = false;
      return;
    }

    let who;
    try {
      who = await api("/api/whoami", { method: "POST", body: "{}" });
    } catch (e) {
      el("loading").hidden = true;
      el("auth-error").hidden = false;
      return;
    }

    el("loading").hidden = true;
    ROLE = who.role;

    if (ROLE === "guest") {
      el("screen-guest").hidden = false;
      el("become-provider-btn").onclick = () => {
        el("screen-guest").hidden = true;
        el("screen-onboarding").hidden = false;
      };
      return;
    }

    if (ROLE === "provider") {
      PROVIDER = who.provider;
      startApp("provider");
      return;
    }

    // client
    try {
      CLIENT_HOME = await api("/api/client/home", { method: "GET" });
    } catch (e) {
      el("loading").hidden = false;
      el("loading").innerHTML = '<div class="empty-state"><div class="empty-emoji">🤔</div>' +
        '<div class="empty-title">Не нашёл твоего специалиста</div>' +
        '<div class="empty-text">Открой приложение по ссылке специалиста ещё раз.</div></div>';
      return;
    }
    startApp("client");
  }

  el("ob-submit").onclick = async () => {
    const name = el("ob-name").value.trim();
    const category = el("ob-category").value.trim();
    if (!name) { showToast("Укажи имя"); return; }
    try {
      await api("/api/provider/register", { method: "POST", body: JSON.stringify({ name, category }) });
    } catch (e) {
      showToast("Не получилось сохранить, попробуй ещё раз");
      return;
    }
    const who = await api("/api/whoami", { method: "POST", body: "{}" });
    PROVIDER = who.provider;
    el("screen-onboarding").hidden = true;
    startApp("provider");
  };

  function startApp(role) {
    ROLE = role;
    el("screen-app").hidden = false;
    const tabs = role === "provider"
      ? [
          { id: "schedule", icon: "🗓", label: "Расписание" },
          { id: "services", icon: "💼", label: "Услуги" },
          { id: "clients", icon: "👥", label: "Клиенты" },
          { id: "profile", icon: "⚙️", label: "Профиль" },
        ]
      : [
          { id: "book", icon: "📅", label: "Запись" },
          { id: "my", icon: "🗂", label: "Мои записи" },
        ];
    el("tabbar").innerHTML = tabs.map((t) =>
      `<button class="tab-item" data-tab="${t.id}"><span class="tab-icon">${t.icon}</span><span>${t.label}</span></button>`
    ).join("");
    Array.from(el("tabbar").querySelectorAll(".tab-item")).forEach((btn) => {
      btn.onclick = () => setTab(btn.dataset.tab);
    });
    setTab(tabs[0].id);
  }

  function setTab(tab) {
    TAB = tab;
    Array.from(el("tabbar").querySelectorAll(".tab-item")).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    if (ROLE === "provider") {
      if (tab === "schedule") renderProviderSchedule();
      if (tab === "services") renderProviderServices();
      if (tab === "clients") renderProviderClients();
      if (tab === "profile") renderProviderProfile();
    } else {
      if (tab === "book") renderClientBook();
      if (tab === "my") renderClientMy();
    }
  }

  // ================= ПРОВАЙДЕР =================

  function setHeader(title, subtitle) {
    el("app-title").textContent = title;
    el("app-subtitle").textContent = subtitle || "";
  }

  async function renderProviderSchedule() {
    setHeader("Расписание", PROVIDER.name);
    const content = el("content");
    content.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api("/api/provider/schedule", { method: "GET" });
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить расписание</div></div>';
      return;
    }

    const byDate = {};
    data.slots.forEach((s) => {
      (byDate[s.date] = byDate[s.date] || []).push(s);
    });
    const dates = Object.keys(byDate).sort();

    let html = "";

    if (data.recent_past && data.recent_past.length) {
      html += '<div class="section-label">Отметить неявку</div><div class="card">';
      data.recent_past.forEach((s, i) => {
        html += `<div class="list-item">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(fmtSlotDt(s.slot_dt))}</div>
            <div class="list-item-sub">${escapeHtml(s.client_name || "")}</div>
          </div>
          <button class="btn btn-sm btn-danger" data-noshow="${s.id}">Не пришёл</button>
        </div>`;
      });
      html += "</div>";
    }

    if (!dates.length) {
      html += '<div class="empty-state"><div class="empty-emoji">🗓</div><div class="empty-title">Пока пусто</div>' +
        '<div class="empty-text">Добавь время кнопкой снизу справа.</div></div>';
    } else {
      dates.forEach((date) => {
        html += `<div class="section-label">${escapeHtml(fmtDay(date))}</div><div class="card">`;
        byDate[date].sort((a, b) => a.time.localeCompare(b.time)).forEach((s) => {
          const booked = s.status === "booked";
          html += `<div class="list-item" data-slot="${s.id}" data-booked="${booked ? 1 : 0}">
            <div class="list-item-main">
              <div class="list-item-title">${s.time}${booked ? " · " + escapeHtml(s.client_name || "") : ""}</div>
              <div class="list-item-sub">${booked ? escapeHtml(s.service_name || "Занято") : "Свободно"}</div>
            </div>
            <span class="badge${booked ? " badge-warn" : ""}">${booked ? "Отменить" : "Удалить"}</span>
          </div>`;
        });
        html += "</div>";
      });
    }

    content.innerHTML = html;

    content.querySelectorAll("[data-noshow]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.noshow;
        showConfirm("Отметить, что клиент не пришёл?", async () => {
          try {
            await api("/api/provider/slots/noshow", { method: "POST", body: JSON.stringify({ slot_id: id }) });
            haptic("success");
            renderProviderSchedule();
          } catch (e) { showToast("Не получилось"); }
        });
      };
    });

    content.querySelectorAll("[data-slot]").forEach((row) => {
      row.onclick = () => {
        const id = row.dataset.slot;
        const booked = row.dataset.booked === "1";
        showConfirm(booked ? "Отменить эту запись? Клиенту придёт уведомление." : "Удалить этот слот?", async () => {
          try {
            await api("/api/provider/slots/cancel", { method: "POST", body: JSON.stringify({ slot_id: id }) });
            haptic("success");
            renderProviderSchedule();
          } catch (e) { showToast("Не получилось"); }
        });
      };
    });

    renderFab(() => openAddSlotSheet());
  }

  function renderFab(onClick) {
    let fab = document.querySelector(".fab-add");
    if (!fab) {
      fab = document.createElement("button");
      fab.className = "fab-add";
      fab.textContent = "+";
      document.body.appendChild(fab);
    }
    fab.hidden = false;
    fab.onclick = onClick;
  }

  function hideFab() {
    const fab = document.querySelector(".fab-add");
    if (fab) fab.hidden = true;
  }

  function fmtSlotDt(slotDt) {
    const [datePart, timePart] = slotDt.split(" ");
    return `${fmtDay(datePart)}, ${timePart}`;
  }

  function openAddSlotSheet() {
    const todayStr = new Date().toISOString().slice(0, 10);
    openSheet(`
      <div class="sheet-title">Добавить время</div>
      <div class="chips" style="margin-bottom:14px">
        <button class="chip active" data-mode="single">Разово</button>
        <button class="chip" data-mode="recur">Еженедельно</button>
      </div>
      <div id="mode-single">
        <div class="field">
          <label class="field-label">Дата</label>
          <input class="input" type="date" id="slot-date" min="${todayStr}" value="${todayStr}" />
        </div>
        <div class="field">
          <label class="field-label">Время</label>
          <input class="input" type="time" id="slot-time" value="10:00" />
        </div>
      </div>
      <div id="mode-recur" hidden>
        <div class="field">
          <label class="field-label">Дни недели</label>
          <div class="chips" id="weekday-chips">
            ${DAYS_RU.map((d, i) => `<button class="chip" data-wd="${i}">${d}</button>`).join("")}
          </div>
        </div>
        <div class="field">
          <label class="field-label">Время</label>
          <input class="input" type="time" id="recur-time" value="10:00" />
        </div>
        <div class="field-hint" style="margin-bottom:14px">Слоты создадутся на 8 недель вперёд.</div>
      </div>
      <button class="btn btn-primary btn-block" id="slot-submit">Добавить</button>
    `);

    const modeChips = Array.from(document.querySelectorAll('[data-mode]'));
    modeChips.forEach((c) => c.onclick = () => {
      modeChips.forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      el("mode-single").hidden = c.dataset.mode !== "single";
      el("mode-recur").hidden = c.dataset.mode !== "recur";
    });

    const wdChips = Array.from(document.querySelectorAll('[data-wd]'));
    wdChips.forEach((c) => c.onclick = () => c.classList.toggle("active"));

    el("slot-submit").onclick = async () => {
      const mode = modeChips.find((c) => c.classList.contains("active")).dataset.mode;
      try {
        if (mode === "single") {
          const date = el("slot-date").value;
          const time = el("slot-time").value;
          if (!date || !time) { showToast("Заполни дату и время"); return; }
          await api("/api/provider/slots/add", {
            method: "POST", body: JSON.stringify({ slot_dt: `${date} ${time}` }),
          });
        } else {
          const weekdays = wdChips.filter((c) => c.classList.contains("active")).map((c) => Number(c.dataset.wd));
          if (!weekdays.length) { showToast("Выбери хотя бы один день"); return; }
          const time = el("recur-time").value;
          const [hh, mm] = time.split(":").map(Number);
          await api("/api/provider/slots/add_recurring", {
            method: "POST", body: JSON.stringify({ weekdays, hh, mm }),
          });
        }
        closeSheet();
        haptic("success");
        renderProviderSchedule();
      } catch (e) {
        showToast(e.status === 409 ? "Такой слот уже есть" : "Не получилось добавить");
      }
    };
  }

  async function renderProviderServices() {
    setHeader("Услуги", PROVIDER.name);
    const content = el("content");
    content.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api("/api/provider/services", { method: "GET" });
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить услуги</div></div>';
      return;
    }
    if (!data.services.length) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">💼</div><div class="empty-title">Пока нет услуг</div>' +
        '<div class="empty-text">Добавь первую услугу кнопкой снизу справа — клиенты будут выбирать из списка при записи.</div></div>';
    } else {
      let html = '<div class="card">';
      data.services.forEach((s) => {
        html += `<div class="list-item" data-service="${s.id}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(s.name)}</div>
            <div class="list-item-sub">${escapeHtml(priceDurationLine(s.price, s.duration_min)) || "Без цены/длительности"}</div>
          </div>
          <span class="badge">Изменить</span>
        </div>`;
      });
      html += "</div>";
      content.innerHTML = html;
      content.querySelectorAll("[data-service]").forEach((row) => {
        const svc = data.services.find((s) => String(s.id) === row.dataset.service);
        row.onclick = () => openServiceSheet(svc);
      });
    }
    renderFab(() => openServiceSheet(null));
  }

  function openServiceSheet(svc) {
    const isEdit = !!svc;
    openSheet(`
      <div class="sheet-title">${isEdit ? "Изменить услугу" : "Новая услуга"}</div>
      <div class="field">
        <label class="field-label">Название</label>
        <input class="input" id="svc-name" type="text" maxlength="80" value="${isEdit ? escapeHtml(svc.name) : ""}" placeholder="Например: Стрижка" />
      </div>
      <div class="input-row field">
        <div style="flex:1">
          <label class="field-label">Цена, ₽</label>
          <input class="input" id="svc-price" type="number" min="0" value="${isEdit && svc.price ? svc.price : ""}" placeholder="—" />
        </div>
        <div style="flex:1">
          <label class="field-label">Длительность, мин</label>
          <input class="input" id="svc-duration" type="number" min="0" value="${isEdit && svc.duration_min ? svc.duration_min : ""}" placeholder="—" />
        </div>
      </div>
      <button class="btn btn-primary btn-block" id="svc-submit" style="margin-bottom:10px">Сохранить</button>
      ${isEdit ? '<button class="btn btn-danger btn-block" id="svc-delete">Удалить услугу</button>' : ""}
    `);

    el("svc-submit").onclick = async () => {
      const name = el("svc-name").value.trim();
      if (!name) { showToast("Укажи название"); return; }
      const priceVal = el("svc-price").value.trim();
      const durationVal = el("svc-duration").value.trim();
      const payload = {
        name,
        price: priceVal ? Number(priceVal) : null,
        duration_min: durationVal ? Number(durationVal) : null,
      };
      try {
        if (isEdit) {
          await api("/api/provider/services/update", { method: "POST", body: JSON.stringify(Object.assign({ id: svc.id }, payload)) });
        } else {
          await api("/api/provider/services/add", { method: "POST", body: JSON.stringify(payload) });
        }
        closeSheet();
        haptic("success");
        renderProviderServices();
      } catch (e) { showToast("Не получилось сохранить"); }
    };

    if (isEdit) {
      el("svc-delete").onclick = () => {
        showConfirm("Удалить услугу?", async () => {
          try {
            await api("/api/provider/services/delete", { method: "POST", body: JSON.stringify({ id: svc.id }) });
            closeSheet();
            renderProviderServices();
          } catch (e) { showToast("Не получилось удалить"); }
        });
      };
    }
  }

  async function renderProviderClients() {
    setHeader("Клиенты", PROVIDER.name);
    hideFab();
    const content = el("content");
    content.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api("/api/provider/clients", { method: "GET" });
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить список</div></div>';
      return;
    }
    if (!data.clients.length) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">👥</div><div class="empty-title">Пока нет клиентов</div>' +
        '<div class="empty-text">Поделись своей ссылкой — вкладка «Профиль».</div></div>';
      return;
    }
    let html = '<div class="card">';
    data.clients.forEach((c) => {
      html += `<div class="list-item">
        <div class="list-item-main">
          <div class="list-item-title">${escapeHtml(c.name || "Без имени")}</div>
          <div class="list-item-sub">${c.username ? "@" + escapeHtml(c.username) : ""}</div>
        </div>
      </div>`;
    });
    html += "</div>";
    content.innerHTML = html;
  }

  async function renderProviderProfile() {
    setHeader("Профиль", "");
    hideFab();
    const content = el("content");
    content.innerHTML = `
      <div class="card">
        <div class="field">
          <label class="field-label">Имя</label>
          <input class="input" id="pf-name" type="text" maxlength="80" value="${escapeHtml(PROVIDER.name)}" />
        </div>
        <div class="field">
          <label class="field-label">Чем занимаешься</label>
          <input class="input" id="pf-category" type="text" maxlength="120" value="${escapeHtml(PROVIDER.category || "")}" />
        </div>
        <button class="btn btn-primary btn-block" id="pf-save">Сохранить</button>
      </div>
      <div class="card">
        <div class="card-title">Твоя ссылка для клиентов</div>
        <p class="muted" style="margin-bottom:10px">Отправь её тем, кого хочешь записывать — по ней они попадут прямо к тебе.</p>
        <input class="input" id="pf-link" type="text" readonly value="${escapeHtml(PROVIDER.link)}" style="margin-bottom:10px" />
        <button class="btn btn-secondary btn-block" id="pf-copy">Скопировать ссылку</button>
      </div>
    `;
    el("pf-save").onclick = async () => {
      const name = el("pf-name").value.trim();
      const category = el("pf-category").value.trim();
      if (!name) { showToast("Укажи имя"); return; }
      try {
        await api("/api/provider/profile", { method: "POST", body: JSON.stringify({ name, category }) });
        PROVIDER.name = name;
        PROVIDER.category = category;
        haptic("success");
        showToast("Сохранено");
        setHeader("Профиль", "");
      } catch (e) { showToast("Не получилось сохранить"); }
    };
    el("pf-copy").onclick = () => {
      const input = el("pf-link");
      input.select();
      try {
        navigator.clipboard.writeText(PROVIDER.link);
        showToast("Ссылка скопирована");
      } catch (e) {
        document.execCommand("copy");
        showToast("Ссылка скопирована");
      }
    };
  }

  // ================= КЛИЕНТ =================

  let selectedServiceId = null;
  let selectedDate = null;

  function renderClientBook() {
    const terms = CLIENT_HOME.terms;
    setHeader(CLIENT_HOME.name, terms.session_pl.charAt(0).toUpperCase() + terms.session_pl.slice(1));
    hideFab();
    const content = el("content");

    let html = "";
    if (CLIENT_HOME.services && CLIENT_HOME.services.length) {
      html += `<div class="section-label">Услуга</div><div class="chips" id="service-chips">`;
      CLIENT_HOME.services.forEach((s, i) => {
        html += `<button class="chip${i === 0 ? " active" : ""}" data-svc="${s.id}">${escapeHtml(s.name)}${s.price ? " · " + s.price + "₽" : ""}</button>`;
      });
      html += "</div>";
      selectedServiceId = CLIENT_HOME.services[0].id;
    }

    if (!CLIENT_HOME.days.length) {
      html += `<div class="empty-state"><div class="empty-emoji">🗓</div><div class="empty-title">Свободного времени пока нет</div>` +
        `<div class="empty-text">Загляни чуть позже.</div></div>`;
      content.innerHTML = html;
      return;
    }

    html += `<div class="section-label">Дата</div><div class="chips" id="day-chips">`;
    CLIENT_HOME.days.forEach((d, i) => {
      html += `<button class="chip${i === 0 ? " active" : ""}" data-date="${d.date}">${escapeHtml(fmtDay(d.date))}</button>`;
    });
    html += `</div><div class="section-label">Время</div><div class="slot-grid" id="slot-grid"></div>`;

    content.innerHTML = html;
    selectedDate = CLIENT_HOME.days[0].date;

    if (el("service-chips")) {
      Array.from(el("service-chips").children).forEach((c) => c.onclick = () => {
        Array.from(el("service-chips").children).forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        selectedServiceId = c.dataset.svc;
      });
    }

    Array.from(el("day-chips").children).forEach((c) => c.onclick = () => {
      Array.from(el("day-chips").children).forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      selectedDate = c.dataset.date;
      renderSlotGrid();
    });

    renderSlotGrid();
  }

  function renderSlotGrid() {
    const grid = el("slot-grid");
    const day = CLIENT_HOME.days.find((d) => d.date === selectedDate);
    grid.innerHTML = "";
    if (!day) return;
    day.slots.forEach((s) => {
      const btn = document.createElement("button");
      btn.className = "slot-btn";
      btn.textContent = s.time;
      btn.onclick = () => confirmBook(s, day.date);
      grid.appendChild(btn);
    });
  }

  function confirmBook(slot, date) {
    const terms = CLIENT_HOME.terms;
    const svc = CLIENT_HOME.services.find((s) => String(s.id) === String(selectedServiceId));
    const svcLine = svc ? ` (${svc.name})` : "";
    showConfirm(`Записаться на ${fmtDay(date)} в ${slot.time}${svcLine}?`, async () => {
      try {
        await api("/api/client/book", {
          method: "POST",
          body: JSON.stringify({ slot_id: slot.id, service_id: selectedServiceId || null }),
        });
        haptic("success");
        showToast("🎉 Записал(а)!");
        CLIENT_HOME = await api("/api/client/home", { method: "GET" });
        renderClientBook();
      } catch (e) {
        haptic("error");
        showToast(e.status === 409 ? "Увы, время уже заняли" : "Не получилось записаться");
        CLIENT_HOME = await api("/api/client/home", { method: "GET" });
        renderClientBook();
      }
    });
  }

  async function renderClientMy() {
    setHeader("Мои записи", "");
    hideFab();
    const content = el("content");
    content.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api("/api/client/my", { method: "GET" });
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить записи</div></div>';
      return;
    }
    if (!data.bookings.length) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">🗂</div><div class="empty-title">Пока нет записей</div>' +
        '<div class="empty-text">Загляни во вкладку «Запись».</div></div>';
      return;
    }
    let html = '<div class="card">';
    data.bookings.forEach((b) => {
      html += `<div class="list-item" data-booking="${b.id}">
        <div class="list-item-main">
          <div class="list-item-title">${escapeHtml(fmtSlotDt(b.slot_dt))}</div>
          <div class="list-item-sub">${escapeHtml(b.trainer_name)}${b.service_name ? " · " + escapeHtml(b.service_name) : ""}</div>
        </div>
        <span class="badge badge-warn">Отменить</span>
      </div>`;
    });
    html += "</div>";
    content.innerHTML = html;
    content.querySelectorAll("[data-booking]").forEach((row) => {
      row.onclick = () => {
        const id = row.dataset.booking;
        showConfirm("Точно отменить запись?", async () => {
          try {
            await api("/api/client/cancel", { method: "POST", body: JSON.stringify({ slot_id: id }) });
            showToast("Отменил запись");
          } catch (e) { showToast("Не получилось отменить"); }
          renderClientMy();
        });
      };
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
