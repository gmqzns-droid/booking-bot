(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const el = (id) => document.getElementById(id);

  const DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const MONTHS_RU = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

  let ROLE = null;         // "provider" | "client" | "guest"
  let PROVIDER = null;     // {id, name, category, terms, link, is_business, staff} — если роль provider
  let CLIENT_HOME = null;  // ответ /api/client/home — если роль client
  let TAB = null;
  let CURRENT_STAFF_ID = null; // выбранный сотрудник (вкладка «Расписание» у провайдера-бизнеса)
  let OB_MODE = "solo";        // выбранный режим на онбординге: "solo" | "business"

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

  const OB_HINTS = {
    solo: "Один специалист — просто ты и твоё расписание.",
    business: "Заведи разных мастеров/сотрудников — клиент сам выберет, к кому записаться.",
  };
  const OB_NAME_LABELS = {
    solo: "Как тебя подписывать клиентам?",
    business: "Название компании/салона",
  };

  Array.from(el("ob-mode-chips").children).forEach((c) => c.onclick = () => {
    Array.from(el("ob-mode-chips").children).forEach((x) => x.classList.remove("active"));
    c.classList.add("active");
    OB_MODE = c.dataset.mode;
    el("ob-mode-hint").textContent = OB_HINTS[OB_MODE];
    el("ob-name-label").textContent = OB_NAME_LABELS[OB_MODE];
  });

  el("ob-submit").onclick = async () => {
    const name = el("ob-name").value.trim();
    const category = el("ob-category").value.trim();
    if (!name) { showToast("Укажи имя"); return; }
    try {
      await api("/api/provider/register", {
        method: "POST",
        body: JSON.stringify({ name, category, is_business: OB_MODE === "business" }),
      });
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
          ...(PROVIDER.is_business ? [{ id: "staff", icon: "🧑‍🤝‍🧑", label: "Сотрудники" }] : []),
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
      if (tab === "staff") renderProviderStaff();
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
    hideFab();
    PROVIDER.staff = PROVIDER.staff || [];

    if (!CURRENT_STAFF_ID || !PROVIDER.staff.some((s) => String(s.id) === String(CURRENT_STAFF_ID))) {
      CURRENT_STAFF_ID = PROVIDER.staff.length ? PROVIDER.staff[0].id : null;
    }
    if (!CURRENT_STAFF_ID) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Нет ни одного сотрудника</div></div>';
      hideFab();
      return;
    }

    let pickerHtml = "";
    if (PROVIDER.is_business && PROVIDER.staff.length > 1) {
      pickerHtml = `<div class="section-label">Сотрудник</div><div class="chips" id="staff-picker-chips">` +
        PROVIDER.staff.map((s) =>
          `<button class="chip${String(s.id) === String(CURRENT_STAFF_ID) ? " active" : ""}" data-staff="${s.id}">${escapeHtml(s.name)}</button>`
        ).join("") + "</div>";
    }

    content.innerHTML = pickerHtml + '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';

    let data;
    try {
      data = await api(`/api/provider/schedule?staff_id=${encodeURIComponent(CURRENT_STAFF_ID)}`, { method: "GET" });
    } catch (e) {
      content.innerHTML = pickerHtml + '<div class="empty-state"><div class="empty-text">Не удалось загрузить расписание</div></div>';
      return;
    }

    const byDate = {};
    data.slots.forEach((s) => {
      (byDate[s.date] = byDate[s.date] || []).push(s);
    });
    const dates = Object.keys(byDate).sort();

    let html = pickerHtml;

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

    if (el("staff-picker-chips")) {
      Array.from(el("staff-picker-chips").children).forEach((c) => c.onclick = () => {
        CURRENT_STAFF_ID = c.dataset.staff;
        renderProviderSchedule();
      });
    }

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
            method: "POST", body: JSON.stringify({ slot_dt: `${date} ${time}`, staff_id: CURRENT_STAFF_ID }),
          });
        } else {
          const weekdays = wdChips.filter((c) => c.classList.contains("active")).map((c) => Number(c.dataset.wd));
          if (!weekdays.length) { showToast("Выбери хотя бы один день"); return; }
          const time = el("recur-time").value;
          const [hh, mm] = time.split(":").map(Number);
          await api("/api/provider/slots/add_recurring", {
            method: "POST", body: JSON.stringify({ weekdays, hh, mm, staff_id: CURRENT_STAFF_ID }),
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
    hideFab();
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

  async function renderProviderStaff() {
    setHeader("Сотрудники", PROVIDER.name);
    hideFab();
    const content = el("content");
    content.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api("/api/provider/staff", { method: "GET" });
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить сотрудников</div></div>';
      return;
    }
    PROVIDER.staff = data.staff;
    if (!data.staff.length) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">🧑‍🤝‍🧑</div><div class="empty-title">Пока нет сотрудников</div>' +
        '<div class="empty-text">Добавь первого сотрудника кнопкой снизу справа.</div></div>';
    } else {
      let html = '<div class="card">';
      data.staff.forEach((s) => {
        html += `<div class="list-item" data-staff="${s.id}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(s.name)}</div>
          </div>
          <span class="badge">Изменить</span>
        </div>`;
      });
      html += "</div>";
      content.innerHTML = html;
      content.querySelectorAll("[data-staff]").forEach((row) => {
        const staff = data.staff.find((s) => String(s.id) === row.dataset.staff);
        row.onclick = () => openStaffSheet(staff);
      });
    }
    renderFab(() => openStaffSheet(null));
  }

  function openStaffSheet(staff) {
    const isEdit = !!staff;
    openSheet(`
      <div class="sheet-title">${isEdit ? "Изменить сотрудника" : "Новый сотрудник"}</div>
      <div class="field">
        <label class="field-label">Имя</label>
        <input class="input" id="staff-name" type="text" maxlength="80" value="${isEdit ? escapeHtml(staff.name) : ""}" placeholder="Например: Мария" />
      </div>
      <button class="btn btn-primary btn-block" id="staff-submit" style="margin-bottom:10px">Сохранить</button>
      ${isEdit ? '<button class="btn btn-danger btn-block" id="staff-delete">Удалить сотрудника</button>' : ""}
    `);

    el("staff-submit").onclick = async () => {
      const name = el("staff-name").value.trim();
      if (!name) { showToast("Укажи имя"); return; }
      try {
        if (isEdit) {
          await api("/api/provider/staff/update", { method: "POST", body: JSON.stringify({ id: staff.id, name }) });
        } else {
          await api("/api/provider/staff/add", { method: "POST", body: JSON.stringify({ name }) });
        }
        closeSheet();
        haptic("success");
        renderProviderStaff();
      } catch (e) { showToast("Не получилось сохранить"); }
    };

    if (isEdit) {
      el("staff-delete").onclick = () => {
        showConfirm("Удалить сотрудника?", async () => {
          try {
            await api("/api/provider/staff/delete", { method: "POST", body: JSON.stringify({ id: staff.id }) });
            closeSheet();
            if (String(CURRENT_STAFF_ID) === String(staff.id)) CURRENT_STAFF_ID = null;
            renderProviderStaff();
          } catch (e) {
            showToast(e.status === 409 ? "Нельзя удалить последнего сотрудника" : "Не получилось удалить");
          }
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
      <div class="card">
        <div class="card-title">Режим работы</div>
        <div class="chips" id="pf-mode-chips">
          <button class="chip${PROVIDER.is_business ? "" : " active"}" data-mode="solo">Я один(а)</button>
          <button class="chip${PROVIDER.is_business ? " active" : ""}" data-mode="business">Команда/салон</button>
        </div>
        <p class="muted" style="margin-top:10px">${PROVIDER.is_business
          ? "Добавляй сотрудников во вкладке «Сотрудники» — клиент сам выбирает мастера."
          : "Один специалист. Переключись на «Команда/салон», если нужно добавить других мастеров."}</p>
      </div>
      <div class="card" id="pf-promos-card">
        <div class="card-title">Промокоды</div>
        <div class="center" style="padding:20px 0"><div class="spinner"></div></div>
      </div>
      <div class="card" id="pf-reviews-card">
        <div class="card-title">Отзывы</div>
        <div class="center" style="padding:20px 0"><div class="spinner"></div></div>
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

    renderPromosCard();

    (async () => {
      const card = el("pf-reviews-card");
      let data;
      try {
        data = await api("/api/provider/reviews", { method: "GET" });
      } catch (e) {
        if (card) card.innerHTML = '<div class="card-title">Отзывы</div><p class="muted">Не удалось загрузить</p>';
        return;
      }
      if (!card) return;
      const summary = data.summary || { count: 0, avg: null };
      let html = '<div class="card-title">Отзывы</div>';
      if (!summary.count) {
        html += '<p class="muted">Пока нет отзывов — появятся после первых визитов.</p>';
      } else {
        html += `<p style="margin-bottom:14px">⭐ <b>${summary.avg}</b> · ${summary.count} ${summary.count === 1 ? "отзыв" : "отзывов"}</p>`;
        data.reviews.filter((r) => r.comment).slice(0, 10).forEach((r) => {
          const who = r.staff_name && r.staff_name !== PROVIDER.name ? ` · ${escapeHtml(r.staff_name)}` : "";
          html += `<div class="list-item" style="display:block">
            <div class="list-item-title">${"⭐".repeat(r.rating)}${who}</div>
            <div class="list-item-sub">${escapeHtml(r.comment)}</div>
          </div>`;
        });
      }
      card.innerHTML = html;
    })();

    Array.from(el("pf-mode-chips").children).forEach((c) => c.onclick = () => {
      const wantBusiness = c.dataset.mode === "business";
      if (wantBusiness === PROVIDER.is_business) return;
      const doSwitch = async () => {
        try {
          await api("/api/provider/profile", { method: "POST", body: JSON.stringify({ is_business: wantBusiness }) });
          const who = await api("/api/whoami", { method: "POST", body: "{}" });
          PROVIDER = who.provider;
          haptic("success");
          showToast("Готово");
          startApp("provider");
        } catch (e) {
          showToast(e.status === 409 ? "Сначала удали лишних сотрудников — оставь одного" : "Не получилось переключить");
        }
      };
      if (wantBusiness) {
        doSwitch();
      } else {
        showConfirm("Вернуться в режим «Я один(а)»? Останется только один сотрудник (ты).", doSwitch);
      }
    });
  }

  async function renderPromosCard() {
    const card = el("pf-promos-card");
    if (!card) return;
    let data;
    try {
      data = await api("/api/provider/promos", { method: "GET" });
    } catch (e) {
      card.innerHTML = '<div class="card-title">Промокоды</div><p class="muted">Не удалось загрузить</p>';
      return;
    }
    let html = '<div class="card-title">Промокоды</div>';
    if (!data.promos.length) {
      html += '<p class="muted" style="margin-bottom:12px">Пока нет ни одного кода.</p>';
    } else {
      data.promos.forEach((p) => {
        const usesLine = p.max_uses ? `${p.used_count}/${p.max_uses} исп.` : `${p.used_count} исп.`;
        html += `<div class="list-item" data-promo="${p.id}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(p.code)} · ${escapeHtml(p.label)}</div>
            <div class="list-item-sub">${usesLine}</div>
          </div>
          <span class="badge badge-warn">Удалить</span>
        </div>`;
      });
    }
    html += '<button class="btn btn-secondary btn-block" id="promo-add-btn" style="margin-top:6px">+ Новый промокод</button>';
    card.innerHTML = html;

    card.querySelectorAll("[data-promo]").forEach((row) => {
      row.onclick = () => {
        const id = row.dataset.promo;
        showConfirm("Удалить промокод?", async () => {
          try {
            await api("/api/provider/promos/delete", { method: "POST", body: JSON.stringify({ id }) });
            renderPromosCard();
          } catch (e) { showToast("Не получилось удалить"); }
        });
      };
    });
    el("promo-add-btn").onclick = openPromoSheet;
  }

  function openPromoSheet() {
    openSheet(`
      <div class="sheet-title">Новый промокод</div>
      <div class="field">
        <label class="field-label">Код</label>
        <input class="input" id="promo-code" type="text" maxlength="20" enterkeyhint="done" placeholder="Например: LETO2026" style="text-transform:uppercase" />
      </div>
      <div class="field">
        <label class="field-label">Тип скидки</label>
        <div class="chips" id="promo-type-chips">
          <button class="chip active" data-type="percent">Процент</button>
          <button class="chip" data-type="fixed">Фикс. сумма</button>
          <button class="chip" data-type="free">Бесплатно</button>
        </div>
      </div>
      <div class="field" id="promo-value-field">
        <label class="field-label" id="promo-value-label">Размер скидки, %</label>
        <input class="input" id="promo-value" type="number" min="1" inputmode="numeric" enterkeyhint="done" placeholder="20" />
      </div>
      <div class="field">
        <label class="field-label">Лимит использований</label>
        <input class="input" id="promo-max-uses" type="number" min="1" inputmode="numeric" enterkeyhint="done" placeholder="Без ограничения" />
      </div>
      <button class="btn btn-primary btn-block" id="promo-submit">Создать</button>
    `);

    [el("promo-code"), el("promo-value"), el("promo-max-uses")].forEach((inp) => {
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); });
    });

    const typeChips = Array.from(el("promo-type-chips").children);
    const valueLabels = { percent: "Размер скидки, %", fixed: "Размер скидки, ₽", free: "" };
    typeChips.forEach((c) => c.onclick = () => {
      typeChips.forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      const type = c.dataset.type;
      el("promo-value-field").hidden = type === "free";
      el("promo-value-label").textContent = valueLabels[type];
    });

    el("promo-submit").onclick = async () => {
      const code = el("promo-code").value.trim().toUpperCase();
      const discount_type = typeChips.find((c) => c.classList.contains("active")).dataset.type;
      if (!code) { showToast("Укажи код"); return; }
      const payload = { code, discount_type };
      if (discount_type !== "free") {
        const val = el("promo-value").value.trim();
        if (!val) { showToast("Укажи размер скидки"); return; }
        payload.discount_value = Number(val);
      }
      const maxUses = el("promo-max-uses").value.trim();
      if (maxUses) payload.max_uses = Number(maxUses);
      try {
        await api("/api/provider/promos/add", { method: "POST", body: JSON.stringify(payload) });
        closeSheet();
        haptic("success");
        renderPromosCard();
      } catch (e) {
        showToast(e.status === 409 ? "Такой код уже есть" : "Не получилось создать");
      }
    };
  }

  // ================= КЛИЕНТ =================

  let selectedServiceId = null;
  let selectedStaffId = null;
  let selectedDate = null;
  let STAFF_DAYS = [];
  let PRESET_STAFF_ID = null;   // выставляется кнопкой «Повторить» из истории записей
  let PRESET_SERVICE_ID = null;
  let ENTERED_PROMO_CODE = null;

  function renderClientBook() {
    const terms = CLIENT_HOME.terms;
    setHeader(CLIENT_HOME.name, terms.session_pl.charAt(0).toUpperCase() + terms.session_pl.slice(1));
    hideFab();
    const content = el("content");

    const presetStaffId = PRESET_STAFF_ID;
    const presetServiceId = PRESET_SERVICE_ID;
    PRESET_STAFF_ID = null;
    PRESET_SERVICE_ID = null;

    let html = "";
    if (CLIENT_HOME.services && CLIENT_HOME.services.length) {
      html += `<div class="section-label">Услуга</div><div class="chips" id="service-chips">`;
      CLIENT_HOME.services.forEach((s) => {
        const isActive = presetServiceId ? String(s.id) === String(presetServiceId) : s.id === CLIENT_HOME.services[0].id;
        html += `<button class="chip${isActive ? " active" : ""}" data-svc="${s.id}">${escapeHtml(s.name)}${s.price ? " · " + s.price + "₽" : ""}</button>`;
      });
      html += "</div>";
      selectedServiceId = presetServiceId || CLIENT_HOME.services[0].id;
    }

    const staffList = CLIENT_HOME.staff || [];
    if (!staffList.length) {
      html += `<div class="empty-state"><div class="empty-emoji">🗓</div><div class="empty-title">Пока нет доступных мастеров</div>` +
        `<div class="empty-text">Загляни чуть позже.</div></div>`;
      content.innerHTML = html;
      return;
    }

    const staffExists = presetStaffId && staffList.some((s) => String(s.id) === String(presetStaffId));

    if (CLIENT_HOME.is_business && staffList.length > 1) {
      html += `<div class="section-label">Мастер</div><div class="chips" id="staff-chips">`;
      staffList.forEach((s) => {
        const isActive = staffExists ? String(s.id) === String(presetStaffId) : s.id === staffList[0].id;
        html += `<button class="chip${isActive ? " active" : ""}" data-staff="${s.id}">${escapeHtml(s.name)}</button>`;
      });
      html += "</div>";
    }

    html += `<div class="field">
      <label class="field-label">Промокод (если есть)</label>
      <div class="input-row">
        <input class="input" id="promo-input" type="text" maxlength="20" enterkeyhint="done" placeholder="Необязательно" style="text-transform:uppercase" value="${escapeHtml(ENTERED_PROMO_CODE || "")}" />
        <button class="btn btn-secondary" id="promo-apply-btn" style="flex:0 0 auto">✓</button>
      </div>
    </div>`;

    html += `<div id="schedule-area"></div>`;

    content.innerHTML = html;
    selectedStaffId = staffExists ? presetStaffId : staffList[0].id;

    el("promo-input").oninput = (e) => { ENTERED_PROMO_CODE = e.target.value.trim().toUpperCase(); };
    el("promo-input").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
    el("promo-apply-btn").onclick = () => {
      el("promo-input").blur();
      if (ENTERED_PROMO_CODE) showToast(`Промокод «${ENTERED_PROMO_CODE}» применится при записи`);
    };

    if (el("service-chips")) {
      Array.from(el("service-chips").children).forEach((c) => c.onclick = () => {
        Array.from(el("service-chips").children).forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        selectedServiceId = c.dataset.svc;
      });
    }

    if (el("staff-chips")) {
      Array.from(el("staff-chips").children).forEach((c) => c.onclick = () => {
        Array.from(el("staff-chips").children).forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        selectedStaffId = c.dataset.staff;
        loadStaffSchedule();
      });
    }

    loadStaffSchedule();
  }

  async function loadStaffSchedule() {
    const area = el("schedule-area");
    if (!area) return;
    area.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api(`/api/client/staff_schedule?staff_id=${encodeURIComponent(selectedStaffId)}`, { method: "GET" });
    } catch (e) {
      area.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить расписание</div></div>';
      return;
    }
    STAFF_DAYS = data.days || [];
    if (!STAFF_DAYS.length) {
      const onWaitlist = !!data.on_waitlist;
      area.innerHTML = `<div class="empty-state"><div class="empty-emoji">🗓</div><div class="empty-title">Свободного времени пока нет</div>` +
        `<div class="empty-text">Загляни чуть позже, или встань в лист ожидания — сообщим, как только время появится.</div></div>` +
        (onWaitlist
          ? `<button class="btn btn-secondary btn-block" id="waitlist-btn">✅ Ты в листе ожидания — нажми, чтобы выйти</button>`
          : `<button class="btn btn-primary btn-block" id="waitlist-btn">🔔 Уведомить, когда освободится</button>`);
      el("waitlist-btn").onclick = async () => {
        try {
          if (onWaitlist) {
            await api("/api/client/waitlist/leave", { method: "POST", body: JSON.stringify({ staff_id: selectedStaffId }) });
            showToast("Убрал(а) из листа ожидания");
          } else {
            await api("/api/client/waitlist/join", {
              method: "POST",
              body: JSON.stringify({ staff_id: selectedStaffId, service_id: selectedServiceId || null }),
            });
            haptic("success");
            showToast("Готово, сообщим при первом освобождении!");
          }
          loadStaffSchedule();
        } catch (e) { showToast("Не получилось, попробуй ещё раз"); }
      };
      return;
    }
    selectedDate = STAFF_DAYS[0].date;
    area.innerHTML = `<div class="section-label">Дата</div><div class="chips" id="day-chips">` +
      STAFF_DAYS.map((d, i) => `<button class="chip${i === 0 ? " active" : ""}" data-date="${d.date}">${escapeHtml(fmtDay(d.date))}</button>`).join("") +
      `</div><div class="section-label">Время</div><div class="slot-grid" id="slot-grid"></div>`;

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
    const day = STAFF_DAYS.find((d) => d.date === selectedDate);
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
          body: JSON.stringify({
            slot_id: slot.id, service_id: selectedServiceId || null,
            promo_code: ENTERED_PROMO_CODE || null,
          }),
        });
        haptic("success");
        showToast(ENTERED_PROMO_CODE ? "🎉 Записал(а), промокод применён!" : "🎉 Записал(а)!");
        ENTERED_PROMO_CODE = null;
        CLIENT_HOME = await api("/api/client/home", { method: "GET" });
        renderClientBook();
      } catch (e) {
        haptic("error");
        const messages = {
          promo_invalid: "Такого промокода нет",
          promo_exhausted: "У промокода закончился лимит",
          promo_used: "Ты уже использовал(а) этот промокод",
        };
        const errCode = (e && e.message) || "";
        showToast(messages[errCode] || (e.status === 409 ? "Увы, время уже заняли" : "Не получилось записаться"));
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
    const whoLine = (b) => (b.staff_name && b.staff_name !== b.trainer_name)
      ? `${escapeHtml(b.staff_name)} (${escapeHtml(b.trainer_name)})`
      : escapeHtml(b.trainer_name);

    let html = "";

    if (!data.bookings.length && !(data.past && data.past.length)) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">🗂</div><div class="empty-title">Пока нет записей</div>' +
        '<div class="empty-text">Загляни во вкладку «Запись».</div></div>';
      return;
    }

    if (data.bookings.length) {
      html += '<div class="section-label">Предстоящие</div><div class="card">';
      data.bookings.forEach((b) => {
        html += `<div class="list-item" data-booking="${b.id}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(fmtSlotDt(b.slot_dt))}</div>
            <div class="list-item-sub">${whoLine(b)}${b.service_name ? " · " + escapeHtml(b.service_name) : ""}${b.discount_label ? " · 🏷 " + escapeHtml(b.discount_label) : ""}</div>
          </div>
          <span class="badge badge-warn">Отменить</span>
        </div>`;
      });
      html += "</div>";
    }

    if (data.past && data.past.length) {
      html += '<div class="section-label">История</div><div class="card">';
      data.past.forEach((b) => {
        html += `<div class="list-item" data-repeat="${b.id}" data-staff="${b.staff_id || ""}" data-service="${b.service_id || ""}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(fmtSlotDt(b.slot_dt))}</div>
            <div class="list-item-sub">${whoLine(b)}${b.service_name ? " · " + escapeHtml(b.service_name) : ""}</div>
          </div>
          <span class="badge">🔁 Повторить</span>
        </div>`;
      });
      html += "</div>";
    }

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
    content.querySelectorAll("[data-repeat]").forEach((row) => {
      row.onclick = () => {
        PRESET_STAFF_ID = row.dataset.staff || null;
        PRESET_SERVICE_ID = row.dataset.service || null;
        setTab("book");
      };
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
