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
    // Приложение использует собственный фирменный стиль ("тёплый бутик"),
    // независимый от цветовой темы Telegram — тему клиента сюда намеренно не подтягиваем.
  }

  // ---------- иконки таббара (inline SVG вместо эмодзи) ----------

  const TAB_ICONS = {
    schedule: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 3v3M16 3v3"/></svg>',
    services: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8 7V5.5A2.5 2.5 0 0 1 10.5 3h3A2.5 2.5 0 0 1 16 5.5V7"/><path d="M3 12h18"/></svg>',
    staff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.6 20c0-3.6 2.9-6.5 6.4-6.5s6.4 2.9 6.4 6.5"/><circle cx="17.5" cy="8.8" r="2.3"/><path d="M15.8 13.6c2.7.5 4.8 2.7 4.8 6"/></svg>',
    branches: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.4"/></svg>',
    clients: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="8.5" cy="8" r="3.2"/><path d="M2 20c0-3.6 2.9-6.5 6.5-6.5S15 16.4 15 20"/><circle cx="17.5" cy="8.8" r="2.3"/><path d="M15.8 13.6c2.7.5 4.8 2.7 4.8 6"/></svg>',
    profile: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.2 14.8a1.6 1.6 0 0 0 .33 1.76l.05.05a1.9 1.9 0 1 1-2.7 2.7l-.05-.05a1.6 1.6 0 0 0-1.76-.33 1.6 1.6 0 0 0-.97 1.47V21a1.9 1.9 0 0 1-3.8 0v-.1a1.6 1.6 0 0 0-1.05-1.47 1.6 1.6 0 0 0-1.76.33l-.05.05a1.9 1.9 0 1 1-2.7-2.7l.05-.05A1.6 1.6 0 0 0 5.1 15a1.6 1.6 0 0 0-1.47-.97H3.5a1.9 1.9 0 0 1 0-3.8h.1A1.6 1.6 0 0 0 5.1 9.2a1.6 1.6 0 0 0-.33-1.76l-.05-.05a1.9 1.9 0 1 1 2.7-2.7l.05.05A1.6 1.6 0 0 0 9.2 4.8a1.6 1.6 0 0 0 .97-1.47V3.2a1.9 1.9 0 0 1 3.8 0v.1a1.6 1.6 0 0 0 .97 1.5 1.6 1.6 0 0 0 1.76-.33l.05-.05a1.9 1.9 0 1 1 2.7 2.7l-.05.05a1.6 1.6 0 0 0-.33 1.76v.03a1.6 1.6 0 0 0 1.47.97h.15a1.9 1.9 0 0 1 0 3.8h-.1a1.6 1.6 0 0 0-1.5.97z"/></svg>',
    book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 3v3M16 3v3"/></svg>',
    my: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  };

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

  // Имя, которое клиент указал сам при записи, вместе с его тг-именем (если они различаются).
  function clientDisplayName(tgName, customName) {
    const tg = tgName || "";
    const custom = (customName || "").trim();
    if (!custom) return tg || "Без имени";
    if (!tg || custom === tg) return custom;
    return `${custom} (тг: ${tg})`;
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

  async function viewAsClient(trainerId) {
    try {
      CLIENT_HOME = await api(`/api/client/home?trainer_id=${encodeURIComponent(trainerId)}`, { method: "GET" });
    } catch (e) { showToast("Не получилось открыть"); return; }
    startApp("client");
  }

  function updateBackToProviderPill() {
    let pill = document.querySelector(".back-pill");
    if (ROLE === "client" && PROVIDER) {
      if (!pill) {
        pill = document.createElement("button");
        pill.className = "back-pill";
        document.body.appendChild(pill);
      }
      pill.textContent = "← Кабинет специалиста";
      pill.hidden = false;
      pill.onclick = () => startApp("provider");
    } else if (pill) {
      pill.hidden = true;
    }
  }

  function startApp(role) {
    ROLE = role;
    el("screen-app").hidden = false;
    updateBackToProviderPill();
    const tabs = role === "provider"
      ? [
          { id: "schedule", icon: "schedule", label: "Расписание" },
          { id: "services", icon: "services", label: "Услуги" },
          ...(PROVIDER.is_business ? [{ id: "branches", icon: "branches", label: "Филиалы" }] : []),
          ...(PROVIDER.is_business ? [{ id: "staff", icon: "staff", label: "Сотрудники" }] : []),
          { id: "clients", icon: "clients", label: "Клиенты" },
          { id: "profile", icon: "profile", label: "Профиль" },
        ]
      : [
          { id: "book", icon: "book", label: "Запись" },
          { id: "my", icon: "my", label: "Мои записи" },
        ];
    el("tabbar").innerHTML = tabs.map((t) =>
      `<button class="tab-item" data-tab="${t.id}"><span class="tab-icon">${TAB_ICONS[t.icon] || ""}</span><span class="tab-label">${t.label}</span></button>`
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
      if (tab === "branches") renderProviderBranches();
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
    const slotsById = {};
    data.slots.forEach((s) => {
      (byDate[s.date] = byDate[s.date] || []).push(s);
      slotsById[s.id] = s;
    });
    const dates = Object.keys(byDate).sort();

    let html = pickerHtml;
    html += `<div style="display:flex;justify-content:flex-end;margin:-6px 0 2px">
      <button class="btn btn-ghost btn-sm" id="close-range-btn">🚫 Закрыть период</button>
    </div>`;

    if (data.recent_past && data.recent_past.length) {
      html += '<div class="section-label">Отметить неявку</div><div class="card">';
      data.recent_past.forEach((s, i) => {
        html += `<div class="list-item">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(fmtSlotDt(s.slot_dt))}</div>
            <div class="list-item-sub">${escapeHtml(clientDisplayName(s.client_name, s.client_custom_name))}</div>
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
              <div class="list-item-title">${s.time}${booked ? " · " + escapeHtml(clientDisplayName(s.client_name, s.client_custom_name)) : ""}</div>
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

    el("close-range-btn").onclick = () => openCloseRangeSheet();

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
        if (booked) {
          openBookedSlotActionsSheet(slotsById[id]);
        } else {
          showConfirm("Удалить этот слот?", async () => {
            try {
              await api("/api/provider/slots/cancel", { method: "POST", body: JSON.stringify({ slot_id: id }) });
              haptic("success");
              renderProviderSchedule();
            } catch (e) { showToast("Не получилось"); }
          });
        }
      };
    });

    renderFab(() => openAddSlotSheet());
  }

  function openBookedSlotActionsSheet(slot) {
    if (!slot) return;
    openSheet(`
      <div class="sheet-title">${escapeHtml(fmtDay(slot.date))}, ${slot.time}</div>
      <p class="muted" style="margin-top:-10px">${escapeHtml(clientDisplayName(slot.client_name, slot.client_custom_name))}${slot.service_name ? " · " + escapeHtml(slot.service_name) : ""}</p>
      <button class="btn btn-secondary btn-block" id="act-reschedule" style="margin-bottom:8px">📅 Перенести</button>
      <button class="btn btn-danger btn-block" id="act-cancel">Отменить запись</button>
    `);
    el("act-reschedule").onclick = () => {
      closeSheet();
      openRescheduleSheet(slot.id, `${fmtDay(slot.date)}, ${slot.time}`);
    };
    el("act-cancel").onclick = () => {
      closeSheet();
      showConfirm("Отменить эту запись? Клиенту придёт уведомление.", async () => {
        try {
          await api("/api/provider/slots/cancel", { method: "POST", body: JSON.stringify({ slot_id: slot.id }) });
          haptic("success");
          renderProviderSchedule();
        } catch (e) { showToast("Не получилось"); }
      });
    };
  }

  function openCloseRangeSheet() {
    const todayStr = new Date().toISOString().slice(0, 10);
    openSheet(`
      <div class="sheet-title">Закрыть период</div>
      <p class="muted" style="margin-top:-8px">Все свободные слоты за этот период исчезнут, а по существующим записям клиентам придёт уведомление об отмене — так что используй для отпуска или выходных, а не просто чтобы освободить пару часов.</p>
      <div class="field">
        <label class="field-label">С какого дня</label>
        <input class="input" id="close-from" type="date" min="${todayStr}" value="${todayStr}" />
      </div>
      <div class="field">
        <label class="field-label">По какой день</label>
        <input class="input" id="close-to" type="date" min="${todayStr}" value="${todayStr}" />
      </div>
      <button class="btn btn-danger btn-block" id="close-range-submit">Закрыть период</button>
    `);
    el("close-range-submit").onclick = async () => {
      const from = el("close-from").value;
      const to = el("close-to").value;
      if (!from || !to) { showToast("Заполни обе даты"); return; }
      if (to < from) { showToast("Дата «по» раньше даты «с»"); return; }
      closeSheet();
      showConfirm(`Закрыть с ${fmtDay(from)} по ${fmtDay(to)}?`, async () => {
        try {
          const res = await api("/api/provider/schedule/close_range", {
            method: "POST",
            body: JSON.stringify({ staff_id: CURRENT_STAFF_ID, start_date: from, end_date: to }),
          });
          haptic("success");
          const parts = [];
          if (res.freed) parts.push(`убрано свободных слотов: ${res.freed}`);
          if (res.cancelled_bookings) parts.push(`отменено записей: ${res.cancelled_bookings}`);
          showToast(parts.length ? `Готово — ${parts.join(", ")}` : "Готово, период закрыт");
          renderProviderSchedule();
        } catch (e) { showToast("Не получилось закрыть период"); }
      });
    };
  }

  function openRescheduleSheet(slotId, currentLabel) {
    const todayStr = new Date().toISOString().slice(0, 10);
    openSheet(`
      <div class="sheet-title">Перенести запись</div>
      <p class="muted" style="margin-top:-8px">Сейчас: ${escapeHtml(currentLabel)}</p>
      <div class="field">
        <label class="field-label">Новая дата</label>
        <input class="input" id="resch-date" type="date" min="${todayStr}" value="${todayStr}" />
      </div>
      <div class="field">
        <label class="field-label">Новое время</label>
        <input class="input" id="resch-time" type="time" />
      </div>
      <button class="btn btn-primary btn-block" id="resch-submit">Перенести</button>
    `);
    el("resch-submit").onclick = async () => {
      const date = el("resch-date").value;
      const time = el("resch-time").value;
      if (!date || !time) { showToast("Заполни дату и время"); return; }
      try {
        await api("/api/provider/slots/reschedule", {
          method: "POST",
          body: JSON.stringify({ slot_id: slotId, new_slot_dt: `${date} ${time}` }),
        });
        closeSheet();
        haptic("success");
        showToast("Перенесено");
        renderProviderSchedule();
      } catch (e) {
        const msg = e.status === 409
          ? (e.message === "slot_conflict" ? "На это время накладывается другая запись по длительности услуги" : "На это время уже есть запись")
          : "Не получилось перенести";
        showToast(msg);
      }
    };
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

    const modeChips = Array.from(el("sheet-card").querySelectorAll('[data-mode]'));
    modeChips.forEach((c) => c.onclick = () => {
      modeChips.forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      el("mode-single").hidden = c.dataset.mode !== "single";
      el("mode-recur").hidden = c.dataset.mode !== "recur";
    });

    const wdChips = Array.from(el("sheet-card").querySelectorAll('[data-wd]'));
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

  async function renderProviderBranches() {
    setHeader("Филиалы", PROVIDER.name);
    hideFab();
    const content = el("content");
    content.innerHTML = '<div class="center" style="padding:40px 0"><div class="spinner"></div></div>';
    let data;
    try {
      data = await api("/api/provider/branches", { method: "GET" });
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить филиалы</div></div>';
      return;
    }
    PROVIDER.branches = data.branches;
    if (!data.branches.length) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">📍</div><div class="empty-title">Пока нет филиалов</div>' +
        '<div class="empty-text">Если у тебя сеть — добавь филиалы с адресами кнопкой снизу справа, и клиент сначала будет выбирать, куда ему удобнее.</div></div>';
    } else {
      let html = '<div class="card">';
      data.branches.forEach((b) => {
        html += `<div class="list-item" data-branch="${b.id}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(b.name)}</div>
            <div class="list-item-sub">${escapeHtml(b.address || "Без адреса")}${b.phone ? " · " + escapeHtml(b.phone) : ""}</div>
          </div>
          <span class="badge">Изменить</span>
        </div>`;
      });
      html += "</div>";
      content.innerHTML = html;
      content.querySelectorAll("[data-branch]").forEach((row) => {
        const branch = data.branches.find((b) => String(b.id) === row.dataset.branch);
        row.onclick = () => openBranchSheet(branch);
      });
    }
    renderFab(() => openBranchSheet(null));
  }

  function openBranchSheet(branch) {
    const isEdit = !!branch;
    openSheet(`
      <div class="sheet-title">${isEdit ? "Изменить филиал" : "Новый филиал"}</div>
      <div class="field">
        <label class="field-label">Название</label>
        <input class="input" id="branch-name" type="text" maxlength="80" value="${isEdit ? escapeHtml(branch.name) : ""}" placeholder="Например: на Ленина" />
      </div>
      <div class="field">
        <label class="field-label">Адрес</label>
        <input class="input" id="branch-address" type="text" maxlength="200" value="${isEdit ? escapeHtml(branch.address || "") : ""}" placeholder="ул. Ленина, 10" />
      </div>
      <div class="field">
        <label class="field-label">Телефон (необязательно)</label>
        <input class="input" id="branch-phone" type="tel" maxlength="40" value="${isEdit ? escapeHtml(branch.phone || "") : ""}" placeholder="+7 999 123-45-67" />
        <div class="field-hint">Если не указать — клиенту покажется общий телефон из профиля (если он там задан).</div>
      </div>
      <button class="btn btn-primary btn-block" id="branch-submit" style="margin-bottom:10px">Сохранить</button>
      ${isEdit ? '<button class="btn btn-danger btn-block" id="branch-delete">Удалить филиал</button>' : ""}
    `);

    el("branch-submit").onclick = async () => {
      const name = el("branch-name").value.trim();
      const address = el("branch-address").value.trim();
      const phone = el("branch-phone").value.trim();
      if (!name) { showToast("Укажи название"); return; }
      try {
        if (isEdit) {
          await api("/api/provider/branches/update", { method: "POST", body: JSON.stringify({ id: branch.id, name, address, phone }) });
        } else {
          await api("/api/provider/branches/add", { method: "POST", body: JSON.stringify({ name, address, phone }) });
        }
        closeSheet();
        haptic("success");
        renderProviderBranches();
      } catch (e) { showToast("Не получилось сохранить"); }
    };

    if (isEdit) {
      el("branch-delete").onclick = () => {
        showConfirm("Удалить филиал? Сотрудники этого филиала останутся без привязки к филиалу.", async () => {
          try {
            await api("/api/provider/branches/delete", { method: "POST", body: JSON.stringify({ id: branch.id }) });
            closeSheet();
            renderProviderBranches();
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
      const calls = [api("/api/provider/staff", { method: "GET" })];
      if (PROVIDER.is_business) calls.push(api("/api/provider/branches", { method: "GET" }));
      const results = await Promise.all(calls);
      data = results[0];
      if (results[1]) PROVIDER.branches = results[1].branches;
    } catch (e) {
      content.innerHTML = '<div class="empty-state"><div class="empty-text">Не удалось загрузить сотрудников</div></div>';
      return;
    }
    PROVIDER.staff = data.staff;
    const branchName = (branchId) => {
      if (!branchId || !PROVIDER.branches) return "";
      const b = PROVIDER.branches.find((x) => String(x.id) === String(branchId));
      return b ? b.name : "";
    };
    if (!data.staff.length) {
      content.innerHTML = '<div class="empty-state"><div class="empty-emoji">🧑‍🤝‍🧑</div><div class="empty-title">Пока нет сотрудников</div>' +
        '<div class="empty-text">Добавь первого сотрудника кнопкой снизу справа.</div></div>';
    } else {
      let html = '<div class="card">';
      data.staff.forEach((s) => {
        const bn = branchName(s.branch_id);
        html += `<div class="list-item" data-staff="${s.id}">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(s.name)}</div>
            ${bn ? `<div class="list-item-sub">📍 ${escapeHtml(bn)}</div>` : ""}
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
    const branches = (PROVIDER.branches || []).filter((b) => b.active !== 0);
    const showBranchSelect = PROVIDER.is_business && branches.length > 0;
    let branchOptions = '<option value="">Без филиала</option>';
    branches.forEach((b) => {
      const selected = isEdit && String(staff.branch_id || "") === String(b.id) ? " selected" : "";
      branchOptions += `<option value="${b.id}"${selected}>${escapeHtml(b.name)}</option>`;
    });
    openSheet(`
      <div class="sheet-title">${isEdit ? "Изменить сотрудника" : "Новый сотрудник"}</div>
      <div class="field">
        <label class="field-label">Имя</label>
        <input class="input" id="staff-name" type="text" maxlength="80" value="${isEdit ? escapeHtml(staff.name) : ""}" placeholder="Например: Мария" />
      </div>
      ${showBranchSelect ? `<div class="field">
        <label class="field-label">Филиал</label>
        <select class="input" id="staff-branch">${branchOptions}</select>
      </div>` : ""}
      <button class="btn btn-primary btn-block" id="staff-submit" style="margin-bottom:10px">Сохранить</button>
      ${isEdit ? '<button class="btn btn-danger btn-block" id="staff-delete">Удалить сотрудника</button>' : ""}
    `);

    el("staff-submit").onclick = async () => {
      const name = el("staff-name").value.trim();
      if (!name) { showToast("Укажи имя"); return; }
      const body = { name };
      if (showBranchSelect) {
        const bv = el("staff-branch").value;
        body.branch_id = bv || null;
      }
      try {
        if (isEdit) {
          body.id = staff.id;
          await api("/api/provider/staff/update", { method: "POST", body: JSON.stringify(body) });
        } else {
          await api("/api/provider/staff/add", { method: "POST", body: JSON.stringify(body) });
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
      const nb = c.next_booking;
      let bookingHtml = '<div class="list-item-sub" style="margin-top:4px">Нет предстоящих записей</div>';
      if (nb) {
        const parts = [fmtSlotDt(nb.slot_dt)];
        if (nb.staff_name) parts.push(nb.staff_name);
        if (nb.service_name) parts.push(nb.service_name);
        bookingHtml = `<div class="list-item-sub" style="margin-top:4px">${escapeHtml(parts.join(" · "))}</div>`;
        if (nb.promo_code) {
          bookingHtml += `<div class="badge badge-accent" style="margin-top:6px">Промокод: ${escapeHtml(nb.discount_label || nb.promo_code)}</div>`;
        }
      }
      html += `<div class="list-item" style="align-items:flex-start; flex-direction:column; gap:10px">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px; width:100%">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(clientDisplayName(c.name, c.custom_name))}${c.blocked ? " 🚫" : ""}</div>
            <div class="list-item-sub">${c.username ? "@" + escapeHtml(c.username) : ""}</div>
            ${bookingHtml}
          </div>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; width:100%">
          ${nb ? `<button class="btn btn-sm btn-danger" data-cancel-slot="${nb.slot_id}">Отменить запись</button>` : ""}
          <button class="btn btn-sm ${c.blocked ? "btn-secondary" : "btn-danger"}" data-block="${c.id}" data-blocked="${c.blocked ? 1 : 0}">
            ${c.blocked ? "Разблокировать" : "Заблокировать"}
          </button>
        </div>
      </div>`;
    });
    html += "</div>";
    content.innerHTML = html;

    content.querySelectorAll("[data-block]").forEach((btn) => {
      btn.onclick = () => {
        const clientId = btn.dataset.block;
        const currentlyBlocked = btn.dataset.blocked === "1";
        const question = currentlyBlocked
          ? "Разблокировать клиента? Сможет снова записываться."
          : "Заблокировать клиента? Больше не сможет записаться к тебе.";
        showConfirm(question, async () => {
          try {
            await api("/api/provider/clients/block", {
              method: "POST",
              body: JSON.stringify({ client_id: clientId, blocked: !currentlyBlocked }),
            });
            haptic("success");
            renderProviderClients();
          } catch (e) { showToast("Не получилось"); }
        });
      };
    });

    content.querySelectorAll("[data-cancel-slot]").forEach((btn) => {
      btn.onclick = () => {
        const slotId = btn.dataset.cancelSlot;
        showConfirm("Отменить эту запись клиента?", async () => {
          try {
            await api("/api/provider/slots/cancel", { method: "POST", body: JSON.stringify({ slot_id: slotId }) });
            haptic("success");
            showToast("Запись отменена");
            renderProviderClients();
          } catch (e) { showToast("Не получилось"); }
        });
      };
    });
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
        <div class="field">
          <label class="field-label">Адрес (необязательно)</label>
          <input class="input" id="pf-address" type="text" maxlength="200" placeholder="Например: ул. Ленина, 10, офис 5" value="${escapeHtml(PROVIDER.address || "")}" />
          <div class="field-hint">Покажется клиенту в «Моих записях» рядом с записью.</div>
        </div>
        <div class="field">
          <label class="field-label">Телефон (необязательно)</label>
          <input class="input" id="pf-phone" type="tel" maxlength="40" placeholder="+7 999 123-45-67" value="${escapeHtml(PROVIDER.phone || "")}" />
          <div class="field-hint">Тоже покажется клиенту в «Моих записях». Если у филиалов свой номер — он укажется в «Филиалах» и заменит этот.</div>
        </div>
        <div class="field">
          <label class="field-label">Не отменять позже чем за N часов</label>
          <input class="input" id="pf-cancel-hours" type="number" min="0" max="168" step="1" value="${PROVIDER.cancel_min_hours || 0}" />
          <div class="field-hint">0 — можно отменять в любой момент. Действует и на перенос записи клиентом.</div>
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
      <div class="card">
        <div class="card-title">Рассылка клиентам</div>
        <p class="muted" style="margin-bottom:10px">Разовое сообщение всем своим клиентам сразу (например «сегодня закрыт(а)» или новость об акции). Заблокированным клиентам не отправляется.</p>
        <textarea class="input textarea" id="bc-text" rows="3" maxlength="1000" placeholder="Текст сообщения..."></textarea>
        <button class="btn btn-primary btn-block" id="bc-send" style="margin-top:10px">Отправить всем</button>
      </div>
      <div class="card" id="pf-stats-card">
        <div class="card-title">Статистика</div>
        <div class="center" style="padding:20px 0"><div class="spinner"></div></div>
      </div>
      <div class="card" id="pf-promos-card">
        <div class="card-title">Промокоды</div>
        <div class="center" style="padding:20px 0"><div class="spinner"></div></div>
      </div>
      <div class="card" id="pf-reviews-card">
        <div class="card-title">Отзывы</div>
        <div class="center" style="padding:20px 0"><div class="spinner"></div></div>
      </div>
      ${PROVIDER.client_links && PROVIDER.client_links.length ? `
      <div class="card">
        <div class="card-title">Ты также клиент</div>
        <p class="muted" style="margin-bottom:10px">Этим же Telegram-аккаунтом ты записан(а) как клиент здесь:</p>
        ${PROVIDER.client_links.map((l) => `
          <button class="btn btn-secondary btn-block" data-view-client="${l.id}" style="margin-bottom:8px">Открыть как клиент — ${escapeHtml(l.name)}</button>
        `).join("")}
      </div>` : ""}
    `;
    el("pf-save").onclick = async () => {
      const name = el("pf-name").value.trim();
      const category = el("pf-category").value.trim();
      const address = el("pf-address").value.trim();
      const phone = el("pf-phone").value.trim();
      const cancelHours = Math.max(0, parseInt(el("pf-cancel-hours").value, 10) || 0);
      if (!name) { showToast("Укажи имя"); return; }
      try {
        await api("/api/provider/profile", {
          method: "POST",
          body: JSON.stringify({ name, category, address, phone, cancel_min_hours: cancelHours }),
        });
        PROVIDER.name = name;
        PROVIDER.category = category;
        PROVIDER.address = address;
        PROVIDER.phone = phone;
        PROVIDER.cancel_min_hours = cancelHours;
        haptic("success");
        showToast("Сохранено");
        setHeader("Профиль", "");
      } catch (e) { showToast("Не получилось сохранить"); }
    };
    content.querySelectorAll("[data-view-client]").forEach((btn) => {
      btn.onclick = () => viewAsClient(btn.dataset.viewClient);
    });
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

    el("bc-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) el("bc-send").click(); });
    el("bc-send").onclick = () => {
      const text = el("bc-text").value.trim();
      if (!text) { showToast("Напиши текст сообщения"); return; }
      showConfirm("Отправить это сообщение всем своим клиентам?", async () => {
        el("bc-send").disabled = true;
        try {
          const res = await api("/api/provider/broadcast", { method: "POST", body: JSON.stringify({ text }) });
          haptic("success");
          showToast(`Отправлено: ${res.sent} из ${res.total}`);
          el("bc-text").value = "";
        } catch (e) {
          showToast("Не получилось отправить");
        } finally {
          el("bc-send").disabled = false;
        }
      });
    };

    renderStatsCard();
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

  let STATS_PERIOD = "week";

  async function renderStatsCard() {
    const card = el("pf-stats-card");
    if (!card) return;
    card.innerHTML = `<div class="card-title">Статистика</div>
      <div class="chips" id="stats-period-chips" style="margin-bottom:10px">
        <button class="chip${STATS_PERIOD === "week" ? " active" : ""}" data-period="week">Неделя</button>
        <button class="chip${STATS_PERIOD === "month" ? " active" : ""}" data-period="month">Месяц</button>
      </div>
      <div id="stats-body" class="center" style="padding:10px 0"><div class="spinner"></div></div>`;
    Array.from(el("stats-period-chips").children).forEach((c) => c.onclick = () => {
      STATS_PERIOD = c.dataset.period;
      renderStatsCard();
    });
    let data;
    try {
      data = await api(`/api/provider/stats?period=${STATS_PERIOD}`, { method: "GET" });
    } catch (e) {
      el("stats-body").innerHTML = '<p class="muted">Не удалось загрузить</p>';
      return;
    }
    el("stats-body").className = "";
    el("stats-body").innerHTML = `
      <div class="card-row"><span class="muted">Визитов</span><b>${data.visits}</b></div>
      <div class="card-row"><span class="muted">Выручка</span><b>${data.revenue ? data.revenue + "₽" : "—"}</b></div>
      <div class="card-row"><span class="muted">Средний чек</span><b>${data.avg_check ? data.avg_check + "₽" : "—"}</b></div>
      <div class="card-row"><span class="muted">Неявки</span><b>${data.no_shows}${data.no_shows ? " (" + data.no_show_rate + "%)" : ""}</b></div>
    `;
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
  let selectedBranchId = null;
  let selectedDate = null;
  let STAFF_DAYS = [];
  let PRESET_STAFF_ID = null;   // выставляется кнопкой «Повторить» из истории записей
  let PRESET_SERVICE_ID = null;
  let ENTERED_PROMO_CODE = null;
  let ENTERED_CLIENT_NAME = null;  // имя, которое клиент указывает при записи (не тг-ник)
  let RESCHEDULE_SLOT_ID = null;   // выставляется кнопкой «Перенести» — какую запись двигаем

  function renderClientBook() {
    const terms = CLIENT_HOME.terms;
    const rescheduling = !!RESCHEDULE_SLOT_ID;
    setHeader(
      rescheduling ? "Перенос записи" : CLIENT_HOME.name,
      rescheduling ? "Выбери новое время" : terms.session_pl.charAt(0).toUpperCase() + terms.session_pl.slice(1),
    );
    hideFab();
    const content = el("content");

    const presetStaffId = PRESET_STAFF_ID;
    const presetServiceId = PRESET_SERVICE_ID;
    PRESET_STAFF_ID = null;
    PRESET_SERVICE_ID = null;

    let html = "";
    if (!rescheduling && CLIENT_HOME.other_businesses && CLIENT_HOME.other_businesses.length) {
      html += `<div class="section-label">Специалист</div><div class="chips" id="business-switch-chips">` +
        `<button class="chip active" data-biz="${CLIENT_HOME.id}">${escapeHtml(CLIENT_HOME.name)}</button>` +
        CLIENT_HOME.other_businesses.map((b) => `<button class="chip" data-biz="${b.id}">${escapeHtml(b.name)}</button>`).join("") +
        `</div>`;
    }
    if (rescheduling) {
      html += `<div class="card" style="margin-bottom:2px">
        <p class="muted" style="margin:0">🔄 Выбери новое время — старая запись освободится автоматически.</p>
        <button class="btn btn-ghost" id="resch-cancel-mode" style="padding:8px 0">Отменить перенос</button>
      </div>`;
    }
    if (CLIENT_HOME.services && CLIENT_HOME.services.length) {
      html += `<div class="section-label">Услуга</div><div class="chips" id="service-chips">`;
      CLIENT_HOME.services.forEach((s) => {
        const isActive = presetServiceId ? String(s.id) === String(presetServiceId) : s.id === CLIENT_HOME.services[0].id;
        html += `<button class="chip${isActive ? " active" : ""}" data-svc="${s.id}">${escapeHtml(s.name)}${s.price ? " · " + s.price + "₽" : ""}</button>`;
      });
      html += "</div>";
      selectedServiceId = presetServiceId || CLIENT_HOME.services[0].id;
    }

    const allStaff = CLIENT_HOME.staff || [];
    const branches = CLIENT_HOME.branches || [];
    const presetStaffBranch = presetStaffId ? (allStaff.find((s) => String(s.id) === String(presetStaffId)) || {}).branch_id : null;
    if (branches.length > 1) {
      const branchStillValid = selectedBranchId && branches.some((b) => String(b.id) === String(selectedBranchId));
      if (presetStaffBranch) {
        selectedBranchId = presetStaffBranch;
      } else if (!branchStillValid) {
        selectedBranchId = branches[0].id;
      }
      html += `<div class="section-label">Филиал</div><div class="chips" id="branch-chips">`;
      branches.forEach((b) => {
        const isActive = String(b.id) === String(selectedBranchId);
        html += `<button class="chip${isActive ? " active" : ""}" data-branch="${b.id}">${escapeHtml(b.name)}</button>`;
      });
      html += "</div>";
    } else {
      selectedBranchId = null;
    }

    const staffList = branches.length > 1
      ? allStaff.filter((s) => String(s.branch_id || "") === String(selectedBranchId))
      : allStaff;

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

    if (!rescheduling) {
      if (ENTERED_CLIENT_NAME === null) ENTERED_CLIENT_NAME = CLIENT_HOME.own_name || "";
      html += `<div class="field">
        <label class="field-label">Ваше имя</label>
        <input class="input" id="client-name-input" type="text" maxlength="80" enterkeyhint="done" placeholder="Как к вам обращаться?" value="${escapeHtml(ENTERED_CLIENT_NAME || "")}" />
        <div class="field-hint">Специалист увидит его вместе с ником в Telegram.</div>
      </div>`;
      html += `<div class="field">
        <label class="field-label">Промокод (если есть)</label>
        <div class="input-row">
          <input class="input" id="promo-input" type="text" maxlength="20" enterkeyhint="done" placeholder="Необязательно" style="text-transform:uppercase" value="${escapeHtml(ENTERED_PROMO_CODE || "")}" />
          <button class="btn btn-secondary" id="promo-apply-btn" style="flex:0 0 auto">✓</button>
        </div>
      </div>`;
    }

    html += `<div id="schedule-area"></div>`;

    content.innerHTML = html;
    selectedStaffId = staffExists ? presetStaffId : staffList[0].id;

    if (el("business-switch-chips")) {
      Array.from(el("business-switch-chips").children).forEach((c) => c.onclick = async () => {
        if (c.classList.contains("active")) return;
        const bizId = c.dataset.biz;
        CLIENT_HOME = await api(`/api/client/home?trainer_id=${encodeURIComponent(bizId)}`, { method: "GET" });
        ENTERED_CLIENT_NAME = null;
        selectedBranchId = null;
        renderClientBook();
      });
    }

    if (el("resch-cancel-mode")) {
      el("resch-cancel-mode").onclick = () => {
        RESCHEDULE_SLOT_ID = null;
        renderClientBook();
      };
    }

    if (el("client-name-input")) {
      el("client-name-input").oninput = (e) => { ENTERED_CLIENT_NAME = e.target.value; };
      el("client-name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
    }

    if (el("promo-input")) {
      el("promo-input").oninput = (e) => { ENTERED_PROMO_CODE = e.target.value.trim().toUpperCase(); };
      el("promo-input").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
      el("promo-apply-btn").onclick = () => {
        el("promo-input").blur();
        if (ENTERED_PROMO_CODE) showToast(`Промокод «${ENTERED_PROMO_CODE}» применится при записи`);
      };
    }

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

    if (el("branch-chips")) {
      Array.from(el("branch-chips").children).forEach((c) => c.onclick = () => {
        if (c.classList.contains("active")) return;
        selectedBranchId = c.dataset.branch;
        renderClientBook();
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
        } catch (e) {
          showToast(e.message === "blocked" ? "К сожалению, запись к этому специалисту сейчас недоступна" : "Не получилось, попробуй ещё раз");
        }
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
    if (RESCHEDULE_SLOT_ID) {
      const oldId = RESCHEDULE_SLOT_ID;
      showConfirm(`Перенести запись на ${fmtDay(date)} в ${slot.time}?`, async () => {
        try {
          await api("/api/client/reschedule", {
            method: "POST",
            body: JSON.stringify({ slot_id: oldId, new_slot_id: slot.id }),
          });
          haptic("success");
          showToast("🔄 Перенесено!");
          RESCHEDULE_SLOT_ID = null;
          setTab("my");
        } catch (e) {
          haptic("error");
          const msg = e.message === "cancel_too_late"
            ? "Переносить уже поздно — свяжись со специалистом напрямую"
            : e.status === 409
              ? (e.message === "slot_conflict" ? "На это время накладывается другая запись" : "Увы, время уже заняли")
              : "Не получилось перенести";
          showToast(msg);
          CLIENT_HOME = await api("/api/client/home", { method: "GET" });
          renderClientBook();
        }
      });
      return;
    }
    const clientName = (ENTERED_CLIENT_NAME || "").trim();
    if (!clientName) {
      showToast("Укажи своё имя перед записью");
      if (el("client-name-input")) el("client-name-input").focus();
      return;
    }
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
            client_name: clientName,
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
          slot_conflict: "На это время накладывается другая запись — выбери другое время",
          blocked: "К сожалению, запись к этому специалисту сейчас недоступна",
          name_required: "Укажи своё имя перед записью",
        };
        const errCode = (e && e.message) || "";
        showToast(messages[errCode] || (e.status === 409 ? "Увы, время уже заняли" : "Не получилось записаться"));
        CLIENT_HOME = await api("/api/client/home", { method: "GET" });
        renderClientBook();
      }
    });
  }

  async function renderClientMy() {
    RESCHEDULE_SLOT_ID = null;   // ушли с экрана переноса, не завершив его — сбрасываем режим
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
        html += `<div class="list-item">
          <div class="list-item-main">
            <div class="list-item-title">${escapeHtml(fmtSlotDt(b.slot_dt))}</div>
            <div class="list-item-sub">${whoLine(b)}${b.service_name ? " · " + escapeHtml(b.service_name) : ""}${b.discount_label ? " · 🏷 " + escapeHtml(b.discount_label) : ""}</div>
            ${b.trainer_address ? `<div class="list-item-sub" style="margin-top:2px">📍 ${b.branch_name ? escapeHtml(b.branch_name) + " · " : ""}${escapeHtml(b.trainer_address)}</div>` : ""}
            ${b.trainer_phone ? `<div class="list-item-sub" style="margin-top:2px">☎ ${escapeHtml(b.trainer_phone)}</div>` : ""}
          </div>
          <div style="display:flex;gap:6px;flex:0 0 auto">
            <button class="btn btn-sm btn-secondary" data-reschedule="${b.id}" data-staff="${b.staff_id || ""}" data-service="${b.service_id || ""}">📅</button>
            <button class="btn btn-sm btn-danger" data-cancel="${b.id}">Отменить</button>
          </div>
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
    content.querySelectorAll("[data-cancel]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.cancel;
        showConfirm("Точно отменить запись?", async () => {
          try {
            await api("/api/client/cancel", { method: "POST", body: JSON.stringify({ slot_id: id }) });
            showToast("Отменил запись");
          } catch (e) {
            showToast(e.message === "cancel_too_late" ? "Отменять уже поздно — свяжись со специалистом напрямую" : "Не получилось отменить");
          }
          renderClientMy();
        });
      };
    });
    content.querySelectorAll("[data-reschedule]").forEach((btn) => {
      btn.onclick = () => {
        RESCHEDULE_SLOT_ID = btn.dataset.reschedule;
        PRESET_STAFF_ID = btn.dataset.staff || null;
        PRESET_SERVICE_ID = btn.dataset.service || null;
        setTab("book");
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
