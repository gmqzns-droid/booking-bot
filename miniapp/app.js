(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const params = new URLSearchParams(window.location.search);
  const trainerId = params.get("trainer_id");

  const DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const MONTHS_RU = ["", "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек"];

  const el = (id) => document.getElementById(id);
  const daysEl = el("days");
  const slotsEl = el("slots");
  const bookEmptyEl = el("book-empty");
  const headerEl = el("header");
  const myListEl = el("my-list");
  const myEmptyEl = el("my-empty");
  const overlayEl = el("confirm-overlay");
  const confirmTextEl = el("confirm-text");
  const toastEl = el("toast");

  let schedule = null;
  let selectedDate = null;

  function applyTheme() {
    if (!tg || !tg.themeParams) return;
    const tp = tg.themeParams;
    const root = document.documentElement.style;
    if (tp.bg_color) root.setProperty("--tg-bg", tp.bg_color);
    if (tp.text_color) root.setProperty("--tg-text", tp.text_color);
    if (tp.hint_color) root.setProperty("--tg-hint", tp.hint_color);
    if (tp.link_color) root.setProperty("--tg-link", tp.link_color);
    if (tp.button_color) root.setProperty("--tg-button", tp.button_color);
    if (tp.button_text_color) root.setProperty("--tg-button-text", tp.button_text_color);
    if (tp.secondary_bg_color) root.setProperty("--tg-secondary-bg", tp.secondary_bg_color);
  }

  function fmtDay(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    const wd = (dt.getDay() + 6) % 7; // JS: 0=Sun -> сдвигаем на Пн=0
    return `${DAYS_RU[wd]}, ${d} ${MONTHS_RU[m]}`;
  }

  function showToast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    setTimeout(() => { toastEl.hidden = true; }, 2500);
  }

  function showConfirm(text, onYes) {
    confirmTextEl.textContent = text;
    overlayEl.hidden = false;
    const yesBtn = el("confirm-yes");
    const noBtn = el("confirm-no");
    const cleanup = () => {
      overlayEl.hidden = true;
      yesBtn.onclick = null;
      noBtn.onclick = null;
    };
    yesBtn.onclick = () => { cleanup(); onYes(); };
    noBtn.onclick = cleanup;
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------- Вкладка "Записаться" ----------

  async function loadSchedule() {
    headerEl.textContent = "Загружаю расписание…";
    try {
      schedule = await api(`/api/schedule?trainer_id=${encodeURIComponent(trainerId)}`);
    } catch (e) {
      headerEl.textContent = "Не удалось загрузить расписание 😔";
      return;
    }
    renderHeader();
    renderDays();
  }

  function renderHeader() {
    if (!schedule) return;
    const parts = [];
    if (schedule.duration_min) parts.push(`${schedule.duration_min} мин`);
    if (schedule.price) parts.push(`${schedule.price}₽`);
    const priceLine = parts.length ? ` · ${parts.join(" · ")}` : "";
    headerEl.innerHTML = `Тренер: <b>${escapeHtml(schedule.trainer_name)}</b>${priceLine}`;
  }

  function renderDays() {
    daysEl.innerHTML = "";
    if (!schedule.days.length) {
      bookEmptyEl.hidden = false;
      slotsEl.innerHTML = "";
      return;
    }
    bookEmptyEl.hidden = true;
    schedule.days.forEach((day, idx) => {
      const btn = document.createElement("button");
      btn.className = "day-chip" + (idx === 0 ? " active" : "");
      btn.textContent = fmtDay(day.date);
      btn.onclick = () => selectDay(day.date, btn);
      daysEl.appendChild(btn);
    });
    selectedDate = schedule.days[0].date;
    renderSlots();
  }

  function selectDay(date, btn) {
    selectedDate = date;
    Array.from(daysEl.children).forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    renderSlots();
  }

  function renderSlots() {
    slotsEl.innerHTML = "";
    const day = schedule.days.find((d) => d.date === selectedDate);
    if (!day) return;
    day.slots.forEach((slot) => {
      const btn = document.createElement("button");
      btn.className = "slot-btn";
      btn.textContent = slot.time;
      btn.onclick = () => confirmBook(slot, day.date);
      slotsEl.appendChild(btn);
    });
  }

  function confirmBook(slot, date) {
    const text = `Записаться на ${fmtDay(date)} в ${slot.time}?`;
    showConfirm(text, async () => {
      try {
        await api("/api/book", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initData: tg ? tg.initData : "", slot_id: slot.id }),
        });
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
        showToast("🎉 Записал(а)!");
        loadSchedule();
      } catch (e) {
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
        showToast(e.status === 409 ? "Увы, слот уже заняли" : "Не получилось записаться");
        loadSchedule();
      }
    });
  }

  // ---------- Вкладка "Мои записи" ----------

  async function loadMyBookings() {
    myListEl.innerHTML = "";
    myEmptyEl.hidden = true;
    let data;
    try {
      data = await api("/api/my", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData: tg ? tg.initData : "" }),
      });
    } catch (e) {
      myEmptyEl.textContent = "Не удалось загрузить записи 😔";
      myEmptyEl.hidden = false;
      return;
    }
    if (!data.bookings.length) {
      myEmptyEl.textContent = "Пока нет записей 🗓";
      myEmptyEl.hidden = false;
      return;
    }
    data.bookings.forEach((b) => {
      const item = document.createElement("div");
      item.className = "my-item";
      const info = document.createElement("div");
      info.className = "my-item-info";
      const [datePart, timePart] = b.slot_dt.split(" ");
      info.innerHTML = `<b>${fmtDay(datePart)}, ${timePart}</b><br>${escapeHtml(b.trainer_name)}`;
      const cancelBtn = document.createElement("button");
      cancelBtn.className = "my-item-cancel";
      cancelBtn.textContent = "❌";
      cancelBtn.onclick = () => confirmCancel(b, datePart, timePart);
      item.appendChild(info);
      item.appendChild(cancelBtn);
      myListEl.appendChild(item);
    });
  }

  function confirmCancel(booking, datePart, timePart) {
    const text = `Точно отменить запись на ${fmtDay(datePart)} в ${timePart}?`;
    showConfirm(text, async () => {
      try {
        await api("/api/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initData: tg ? tg.initData : "", slot_id: booking.id }),
        });
        showToast("Отменил запись");
      } catch (e) {
        showToast("Не получилось отменить");
      }
      loadMyBookings();
    });
  }

  // ---------- Вкладки ----------

  function setupTabs() {
    const tabs = document.querySelectorAll(".tab");
    tabs.forEach((tab) => {
      tab.onclick = () => {
        tabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const target = tab.dataset.tab;
        el("book-view").hidden = target !== "book";
        el("my-view").hidden = target !== "my";
        if (target === "my") loadMyBookings();
      };
    });
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function init() {
    if (tg) {
      tg.ready();
      tg.expand();
      applyTheme();
    }
    setupTabs();
    if (!trainerId) {
      headerEl.textContent = "Не нашёл тренера — открой запись из бота ещё раз.";
      return;
    }
    loadSchedule();
    if (window.location.hash === "#my") {
      document.querySelector('.tab[data-tab="my"]').click();
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
