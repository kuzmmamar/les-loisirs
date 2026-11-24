const savedPerson = (() => {
  try {
    const p = new URLSearchParams(window.location.search).get("who") || localStorage.getItem("parter_active_person");
    return p === "+1" || p === "А+1" || p === "К" ? "+1" : "А";
  } catch (_) {
    return "А";
  }
})();

const state = {
  activePerson: savedPerson,
  tab: "swipe",
  initialTabChosen: false,
  repFilter: "inbox",
  directorFilter: "all",
  weekIndex: 0,
  expandedBusyDates: {},
  swipeHistoryByPerson: { А: [], "+1": [] },
  data: null,
  // Для каждого показа хранит { count: 1|2|3, ids: [id1, id2...] }
  eventSelection: {},
};

const awayCalState = {
  person: "А",
  baseYear: 2026,
  baseMonth: 10, // 0-indexed (10 = Ноябрь, 11 = Декабрь)
  startIso: "",
  endIso: "",
  isDragging: false,
};

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

function fmtRub(n) {
  return new Intl.NumberFormat("ru-RU").format(n || 0) + " ₽";
}

function iconGoldenMask(size = 16) {
  return `<svg class="gm-mask-svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="Золотая Маска"><path d="M5 4.2C7.3 3.1 9.6 2.7 12 2.7c2.4 0 4.7.4 7 1.5.9.4 1.5 1.3 1.5 2.3v4.8c0 5.4-3.7 9.4-8.5 10.7C7.2 20.7 3.5 16.7 3.5 11.3V6.5c0-1 .6-1.9 1.5-2.3Z" fill="#E5C175" stroke="#F7DF9E" stroke-width="1.15"/><path d="M12 2.8v19.1" stroke="#A67E30" stroke-width="0.95" opacity="0.6"/><path d="M6.6 9.6c1-1.2 2.5-1.2 3.5 0-1 .65-2.5.65-3.5 0Zm7.3 0c1-1.2 2.5-1.2 3.5 0-1 .65-2.5.65-3.5 0Z" fill="#16151C"/><path d="M8.6 14.7c1 .95 2.2 1.35 3.4 1.35 1.2 0 2.4-.4 3.4-1.35" stroke="#16151C" stroke-width="1.75" stroke-linecap="round"/></svg>`;
}

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function switchTab(newTab) {
  state.tab = newTab;
  document.querySelectorAll(".tab-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === newTab);
  });
  window.scrollTo(0, 0);
  renderAll();
}
window.switchTab = switchTab;

function openSheet(html) {
  document.getElementById("sheetContent").innerHTML = html;
  document.getElementById("sheetBackdrop").classList.remove("hidden");
}

function closeSheet() {
  document.getElementById("sheetBackdrop").classList.add("hidden");
}

document.getElementById("sheetBackdrop").addEventListener("click", (e) => {
  if (e.target.id === "sheetBackdrop") closeSheet();
});

async function loadState() {
  const res = await fetch(`/api/state?person=${encodeURIComponent(state.activePerson)}`);
  state.data = await res.json();

  // Поддержка прямого открытия вкладки (?tab=week или #week) + автопереход если колода пуста
  if (!state.initialTabChosen) {
    state.initialTabChosen = true;
    const urlTab = new URLSearchParams(window.location.search).get("tab") || window.location.hash.replace("#", "");
    if (["swipe", "week", "liked", "tickets", "profile"].includes(urlTab)) {
      switchTab(urlTab);
      maybeOpenPostShowPrompt();
      return;
    }
    const inboxCards = (state.data.repertoire || []).filter((p) => p.status === "inbox");
    if (inboxCards.length === 0) {
      switchTab("week");
      maybeOpenPostShowPrompt();
      return;
    }
  }
  renderAll();
  maybeOpenPostShowPrompt();
}

window.switchActivePerson = async (person) => {
  const norm = person === "+1" || person === "А+1" || person === "К" ? "+1" : "А";
  if (state.activePerson === norm && state.data) {
    syncSliderVisual(document.getElementById("topPersonSlider"), norm);
    return;
  }
  state.activePerson = norm;
  syncSliderVisual(document.getElementById("topPersonSlider"), norm);
  try {
    localStorage.setItem("parter_active_person", norm);
  } catch (_) {}
  state.directorFilter = "all";
  await loadState();
};

function syncSliderVisual(sliderEl, val) {
  if (!sliderEl) return;
  const norm = val === "+1" ? "+1" : "А";
  sliderEl.dataset.person = norm;
  sliderEl.classList.remove("dragging");
  const thumb = sliderEl.querySelector(".person-slider-thumb");
  if (thumb) {
    thumb.style.transform = norm === "+1" ? "translateX(100%)" : "translateX(0%)";
  }
  sliderEl.querySelectorAll(".person-slider-opt").forEach((b) => {
    b.classList.toggle("active", b.dataset.val === norm);
  });
}

function bindPersonSlider(sliderEl, getCurrent, onChoose) {
  if (!sliderEl || sliderEl.dataset.bound === "1") return;
  sliderEl.dataset.bound = "1";
  const thumb = sliderEl.querySelector(".person-slider-thumb");

  let active = false;
  let startX = 0;
  let startRatio = 0;
  let curRatio = 0;
  let moved = false;

  const begin = (clientX) => {
    active = true;
    moved = false;
    startX = clientX;
    startRatio = getCurrent() === "+1" ? 1 : 0;
    curRatio = startRatio;
    sliderEl.classList.add("dragging");
  };

  const move = (clientX) => {
    if (!active) return;
    const dx = clientX - startX;
    if (Math.abs(dx) > 3) moved = true;
    const halfW = Math.max(18, (sliderEl.clientWidth - 4) / 2);
    curRatio = Math.max(0, Math.min(1, startRatio + dx / halfW));
    if (thumb) {
      thumb.style.transform = `translateX(${(curRatio * 100).toFixed(1)}%)`;
    }
    const preview = curRatio >= 0.5 ? "+1" : "А";
    sliderEl.querySelectorAll(".person-slider-opt").forEach((b) => {
      b.classList.toggle("active", b.dataset.val === preview);
    });
  };

  const finish = (clientX) => {
    if (!active) return;
    active = false;
    sliderEl.classList.remove("dragging");
    let chosen;
    if (!moved) {
      const rect = sliderEl.getBoundingClientRect();
      chosen = clientX >= rect.left + rect.width / 2 ? "+1" : "А";
    } else {
      chosen = curRatio >= 0.5 ? "+1" : "А";
    }
    syncSliderVisual(sliderEl, chosen);
    onChoose(chosen);
  };

  sliderEl.addEventListener(
    "touchstart",
    (e) => {
      if (!e.touches.length) return;
      begin(e.touches[0].clientX);
    },
    { passive: true }
  );
  sliderEl.addEventListener(
    "touchmove",
    (e) => {
      if (!active || !e.touches.length) return;
      e.preventDefault();
      move(e.touches[0].clientX);
    },
    { passive: false }
  );
  sliderEl.addEventListener("touchend", (e) => {
    const cx = e.changedTouches && e.changedTouches.length ? e.changedTouches[0].clientX : startX;
    finish(cx);
  });

  sliderEl.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;
    try {
      sliderEl.setPointerCapture(e.pointerId);
    } catch (_) {}
    begin(e.clientX);
  });
  sliderEl.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    move(e.clientX);
  });
  sliderEl.addEventListener("pointerup", (e) => {
    if (e.pointerType === "touch") return;
    finish(e.clientX);
  });
}

function getDefaultAKIds(comps) {
  const a = comps.find((c) => c.name === "А");
  const k = comps.find((c) => c.name === "+1" || c.name === "А+1" || c.name === "К");
  if (a && k) return [a.id, k.id];
  return comps.slice(0, 2).map((c) => c.id);
}

function sortAwayAFirst(list) {
  const rank = (name) => (name === "А" ? 0 : name === "+1" ? 1 : 2);
  return [...(list || [])].sort((x, y) => {
    const r = rank(x.person_name) - rank(y.person_name);
    if (r !== 0) return r;
    return String(x.start_date || "").localeCompare(String(y.start_date || ""));
  });
}

function groupAwayByPerson(list) {
  const sorted = sortAwayAFirst(list);
  const groups = [];
  ["А", "+1"].forEach((pName) => {
    const items = sorted.filter((a) => a.person_name === pName);
    if (items.length > 0) {
      groups.push({
        person_name: pName,
        items,
        rangesText: items.map((a) => fmtShortRange(a.start_date, a.end_date)).join("; "),
        firstStart: items[0].start_date,
      });
    }
  });
  return groups;
}

function fmtShortRange(startIso, endIso) {
  const fmtOne = (iso) => {
    const parts = (iso || "").split("-");
    return parts.length === 3 ? `${parts[2]}.${parts[1]}` : iso;
  };
  return startIso === endIso ? fmtOne(startIso) : `${fmtOne(startIso)}-${fmtOne(endIso)}`;
}

function fmtCompactAwayRange(startIso, endIso) {
  const s = (startIso || "").split("-");
  const e = (endIso || startIso || "").split("-");
  if (s.length !== 3 || e.length !== 3) return startIso;
  const d1 = String(Number(s[2]));
  const m1 = s[1];
  const d2 = String(Number(e[2]));
  const m2 = e[1];
  if (startIso === endIso) return `${d1}.${m1}`;
  if (s[0] === e[0] && m1 === m2) return `${d1}–${d2}.${m1}`;
  return `${d1}.${m1}–${d2}.${m2}`;
}

function renderAwayTopBar() {
  const awayPeriods = sortAwayAFirst((state.data && state.data.away_periods) || []);
  return `
    <div class="away-bar-wrap">
      <button type="button" class="away-add-btn" onclick="openAwaySheet()">Нет в Мск +</button>
      ${awayPeriods
        .map(
          (a) => `
        <span class="away-pill" role="button" tabindex="0" style="cursor:pointer;" onclick="jumpToDateInSchedule('${a.start_date}')">
          <span>${a.person_name}:${fmtCompactAwayRange(a.start_date, a.end_date)}</span>
          <button type="button" class="away-pill-x" title="Убрать" onclick="event.stopPropagation(); deleteAwayPeriod(${a.id})">✕</button>
        </span>
      `
        )
        .join("")}
    </div>
  `;
}

function fmtPricePerTicket(ev) {
  if (!ev) return "";
  const pMin = Number(ev.price_min || 0);
  const pMax = Number(ev.price_max || 0);
  const est = Number(ev.est_ticket_price || 0);
  if (pMin > 0 && pMax > 0) {
    if (pMin === pMax) return `${pMin.toLocaleString("ru-RU")} руб./бил.`;
    return `${pMin.toLocaleString("ru-RU")}–${pMax.toLocaleString("ru-RU")} руб./бил.`;
  }
  if (est > 0) return `~${est.toLocaleString("ru-RU")} руб./бил.`;
  return "";
}

function bindDismissSwipeLeft(el, onDismiss) {
  if (!el || el.dataset.dismissSwipeBound === "1") return;
  el.dataset.dismissSwipeBound = "1";
  el.classList.add("swipe-dismissable");

  let startX = 0;
  let startY = 0;
  let dx = 0;
  let tracking = false;
  let lockedHoriz = false;

  const begin = (x, y) => {
    startX = x;
    startY = y;
    dx = 0;
    tracking = true;
    lockedHoriz = false;
    el.style.transition = "none";
  };

  const move = (x, y, preventFn) => {
    if (!tracking) return;
    const curDx = x - startX;
    const curDy = y - startY;
    if (!lockedHoriz) {
      if (Math.abs(curDy) > 12 && Math.abs(curDy) > Math.abs(curDx)) {
        tracking = false;
        el.style.transform = "";
        el.style.opacity = "";
        return;
      }
      if (curDx < -8 && Math.abs(curDx) > Math.abs(curDy) * 1.1) {
        lockedHoriz = true;
      }
    }
    if (lockedHoriz) {
      if (preventFn) preventFn();
      dx = Math.min(0, curDx);
      el.style.transform = `translateX(${dx}px)`;
      el.style.opacity = String(Math.max(0.25, 1 + dx / 260));
    }
  };

  const finish = () => {
    if (!tracking) return;
    tracking = false;
    if (lockedHoriz && dx < -65) {
      el.dataset.justSwiped = "1";
      el.style.transition = "transform 0.2s ease-out, opacity 0.2s ease-out";
      el.style.transform = "translateX(-420px)";
      el.style.opacity = "0";
      setTimeout(() => {
        el.style.transition = "";
        el.style.transform = "";
        el.style.opacity = "";
        delete el.dataset.justSwiped;
        onDismiss();
      }, 190);
    } else {
      el.style.transition = "transform 0.18s ease, opacity 0.18s ease";
      el.style.transform = "";
      el.style.opacity = "";
    }
  };

  el.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length === 1) begin(e.touches[0].clientX, e.touches[0].clientY);
    },
    { passive: true }
  );
  el.addEventListener(
    "touchmove",
    (e) => {
      if (e.touches.length === 1) move(e.touches[0].clientX, e.touches[0].clientY, () => e.preventDefault());
    },
    { passive: false }
  );
  el.addEventListener("touchend", finish);
  el.addEventListener("touchcancel", finish);

  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;
    if (e.target.closest("button, a")) return;
    begin(e.clientX, e.clientY);
  });
  el.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    move(e.clientX, e.clientY, null);
  });
  el.addEventListener("pointerup", (e) => {
    if (e.pointerType === "touch") return;
    finish();
  });
}

function getEventSel(ev) {
  if (!state.eventSelection[ev.event_id]) {
    const comps = (state.data && state.data.companions) || [];
    if (ev.party_ids && ev.party_ids.length === 1) {
      state.eventSelection[ev.event_id] = { count: 1, ids: [...ev.party_ids] };
    } else if (ev.other_away_names && ev.other_away_names.length > 0) {
      const me = comps.find((c) => c.name === state.activePerson) || comps[0];
      state.eventSelection[ev.event_id] = { count: 1, ids: me ? [me.id] : [] };
    } else {
      const akIds = getDefaultAKIds(comps);
      state.eventSelection[ev.event_id] = { count: 2, ids: akIds };
    }
  }
  return state.eventSelection[ev.event_id];
}

function renderCompactPartyBar(eventId, sel, comps, sheetSlug = null) {
  const qtyFn = (q) =>
    sheetSlug ? `setSheetTicketQty('${sheetSlug}', ${eventId}, ${q})` : `setTicketQty(${eventId}, ${q})`;
  const whoFn = (cid) =>
    sheetSlug ? `toggleSheetPerson('${sheetSlug}', ${eventId}, ${cid})` : `toggleEventPerson(${eventId}, ${cid})`;

  return `
    <div class="compact-party-bar">
      <div class="mini-qty-seg">
        <button type="button" class="mini-qty-btn ${sel.count === 1 ? "active" : ""}" onclick="${qtyFn(1)}">1</button>
        <button type="button" class="mini-qty-btn ${sel.count === 2 ? "active" : ""}" onclick="${qtyFn(2)}">2 рядом</button>
      </div>
      <div class="mini-letters-row">
        <span class="mini-who-lbl">Кто:</span>
        <div class="person-pair-pill">
          ${comps
            .filter((c) => c.name === "А" || c.name === "+1")
            .map((c) => {
              const checked = sel.ids.includes(c.id);
              return `<button type="button" class="person-pair-btn ${checked ? "active" : ""}" onclick="${whoFn(c.id)}">${c.name}</button>`;
            })
            .join("")}
        </div>
      </div>
    </div>
  `;
}

function syncTopbarStickyOffset() {
  const tb = document.querySelector(".topbar");
  if (tb) {
    const h = Math.ceil(tb.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty("--topbar-h", `${h + 4}px`);
  }
}
window.addEventListener("resize", syncTopbarStickyOffset);

function renderTopAndBanner() {
  syncTopbarStickyOffset();
  const topSlider = document.getElementById("topPersonSlider");
  bindPersonSlider(topSlider, () => state.activePerson, (p) => switchActivePerson(p));
  syncSliderVisual(topSlider, state.activePerson);

  const inboxCount = (state.data.repertoire || []).filter((p) => p.status === "inbox").length;
  const inboxBadge = document.getElementById("inboxBadge");
  if (inboxCount > 0) {
    inboxBadge.textContent = String(inboxCount);
    inboxBadge.classList.remove("hidden");
  } else {
    inboxBadge.classList.add("hidden");
  }

  const likedPlaysLen = ((state.data.liked && state.data.liked.liked_plays) || []).length;
  const creatorsLen = ((state.data.liked && state.data.liked.tracked_creators) || []).length;
  const likedCount = likedPlaysLen + creatorsLen;
  const postShowPending = ((state.data.post_show && state.data.post_show.pending) || []);
  const likedBadge = document.getElementById("likedBadge");
  if (likedBadge) {
    if (likedCount > 0) {
      likedBadge.textContent = String(likedCount);
      likedBadge.classList.remove("hidden");
    } else {
      likedBadge.classList.add("hidden");
    }
  }

  const walletCount = (state.data.wallet_tickets || []).length;
  const pendingOrders = (state.data.orders || []).filter((o) => o.status === "awaiting_bank_push");
  const badge = document.getElementById("ticketsBadge");
  const totalBadge = walletCount + pendingOrders.length;
  if (totalBadge > 0) {
    badge.textContent = String(totalBadge);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const banner = document.getElementById("activeOrderBanner");
  if (pendingOrders.length > 0) {
    const o = pendingOrders[0];
    banner.classList.remove("hidden");
    delete banner.dataset.dismissSwipeBound;
    banner.innerHTML = `
      <div style="min-width:0;">
        <div class="bank-banner-title">Подтвердите пуш в банке (бронь до ${o.hold_expires_at})</div>
        <div class="bank-banner-sub">${o.production_title} · ${o.seat_selection} · ${fmtRub(o.total_price)} · свайп ← отменит</div>
      </div>
      <span class="pill-btn" style="flex-shrink:0;">Открыть →</span>
    `;
    banner.onclick = () => {
      if (banner.dataset.justSwiped === "1") return;
      openOrderConfirmSheet(o);
    };
    bindDismissSwipeLeft(banner, () => cancelOrder(o.id));
  } else if (postShowPending.length > 0) {
    const rv = postShowPending[0];
    banner.classList.remove("hidden");
    banner.style.transform = "";
    banner.style.opacity = "";
    banner.innerHTML = `
      <div>
        <div class="bank-banner-title">После спектакля: понравилось или нет?</div>
        <div class="bank-banner-sub">«${rv.title}»${rv.venue_name ? " · " + rv.venue_name : ""}</div>
      </div>
      <span class="pill-btn">Оценить →</span>
    `;
    banner.onclick = () => openPostShowSheet(rv);
  } else {
    banner.classList.add("hidden");
  }
}

/* ================= ВКЛАДКА 1: ЛЕНТА СПЕКТАКЛЕЙ ДРУГ ЗА ДРУГОМ + СВАЙПЫ ================= */
function renderSwipeTab() {
  const all = state.data.repertoire || [];
  const inboxList = all.filter((p) => p.status === "inbox");
  const wantCount = all.filter((p) => p.status === "want" || p.status === "sniper").length;

  const specialFilters = ["Золотая Маска", "МХТ"];
  const rawDirs = Array.from(new Set(all.map((p) => p.director).filter(Boolean)));
  const directors = [
    ...specialFilters,
    ...rawDirs.filter((d) => !specialFilters.includes(d)),
  ];

  let items = all;
  if (state.repFilter === "inbox") items = inboxList;
  else if (state.repFilter === "want") items = all.filter((p) => p.status === "want" || p.status === "sniper");

  if (state.directorFilter === "Золотая Маска") {
    items = items.filter((p) => Boolean(p.is_golden_mask) || p.director === "Золотая Маска");
  } else if (state.directorFilter === "МХТ") {
    items = items.filter((p) => p.venue_id === "mxat" || p.director === "МХТ");
  } else if (state.directorFilter !== "all") {
    items = items.filter((p) => p.director === state.directorFilter);
  }

  const filters = [
    ["inbox", `К отбору (${inboxList.length})`],
    ["all", `Все (${all.length})`],
    ["want", `Хочу (${wantCount})`],
  ];

  const myHistory = state.swipeHistoryByPerson[state.activePerson] || [];

  return `
    ${renderAwayTopBar()}

    <div class="swipe-top-row">
      <span class="swipe-counter">
        ${inboxList.length > 0 ? `Для ${state.activePerson} · осталось: ${inboxList.length}` : `Для ${state.activePerson} всё разобрано`}
      </span>
      <div style="display:flex;gap:6px;">
        ${
          myHistory.length
            ? `<button type="button" class="pill-btn" onclick="undoLastSwipe()">↩ Отменить</button>`
            : ""
        }
        <button type="button" class="pill-btn" onclick="switchTab('week')">К расписанию →</button>
      </div>
    </div>

    <div class="swipe-hints-bar">
      <span style="color:var(--danger);font-weight:700;">← "Не хочу"</span>
      <span style="color:var(--gold);font-weight:700;">"Уже был" - 2x tap</span>
      <span style="color:var(--green);font-weight:700;">"Хочу" →</span>
    </div>

    <div class="filter-pills-row">
      ${filters
        .map(
          ([key, label]) => `
        <button type="button" class="filter-chip ${state.repFilter === key ? "active" : ""}" onclick="setRepFilter('${key}')">
          ${label}
        </button>
      `
        )
        .join("")}
      <select class="director-select" onchange="setDirectorFilter(this.value)">
        <option value="all" ${state.directorFilter === "all" ? "selected" : ""}>Авторы: все ▾</option>
        ${directors
          .map(
            (dir) => `
          <option value="${dir}" ${state.directorFilter === dir ? "selected" : ""}>${dir}</option>
        `
          )
          .join("")}
      </select>
    </div>

    ${
      items.length === 0
        ? `
      <div class="card" style="text-align:center;padding:24px 16px;">
        <div style="font-size:30px;margin-bottom:8px;">✨</div>
        <h3 style="margin:0 0 6px;font-size:18px;">${state.repFilter === "inbox" ? "Все новые спектакли разобраны" : "В этом фильтре пусто"}</h3>
        <div class="venue-line" style="line-height:1.45;margin-bottom:14px;">
          Отмеченные спектакли «Хочу» разложены по дням недели в вашем расписании с лучшими местами.
        </div>
        <button type="button" class="btn-primary" style="width:100%;padding:12px;" onclick="switchTab('week')">
          Открыть расписание по дням →
        </button>
        ${
          state.repFilter === "inbox"
            ? `<button type="button" class="btn-ghost" style="width:100%;margin-top:8px;" onclick="setRepFilter('all')">Показать все спектакли (${all.length})</button>`
            : ""
        }
      </div>
    `
        : items.map((it) => renderFeedSwipeCard(it)).join("")
    }
  `;
}

function renderFeedSwipeCard(it) {
  const isWant = it.status === "want" || it.status === "sniper";
  const evs = it.upcoming_events || [];
  const firstEv = evs[0] || null;
  const roleLabel = it.director_role || (it.category === "ballet" ? "Хореограф" : "Режиссёр");
  const creatorName = it.stage_director || it.director;

  let datesCornerHtml = `<div class="date-none-badge">Ждём старт<br/>продаж</div>`;
  if (firstEv) {
    const secondEv = evs[1] || null;
    const firstLine = `${firstEv.weekday}, ${firstEv.label} · ${firstEv.event_time}`;
    const firstPriceStr = fmtPricePerTicket(firstEv);

    if (!secondEv) {
      datesCornerHtml = `
        <div class="dates-dd-wrap">
          <button type="button" class="dates-dd-trigger ${firstEv.other_away_label ? "away-date" : ""}"
            onclick="selectDateFromDropdown('${it.slug}', ${firstEv.event_id}, event)">
            <span class="dd-main">${firstLine} ›</span>
            ${firstEv.other_away_label ? `<span class="dd-sub">${firstEv.other_away_label}</span>` : ""}
          </button>
          ${firstPriceStr ? `<div class="date-price-under">${firstPriceStr}</div>` : ""}
        </div>
      `;
    } else {
      const secondLine = `${secondEv.weekday}, ${secondEv.label} · ${secondEv.event_time} ▾`;
      datesCornerHtml = `
        <div class="dates-dd-wrap">
          <button type="button" class="dates-dd-trigger ${firstEv.other_away_label && secondEv.other_away_label ? "away-date" : ""}"
            onclick="toggleDatesDropdown('${it.slug}', event)">
            <span class="dd-main">${firstLine}</span>
            <span class="dd-sub">${secondLine}</span>
          </button>
          ${firstPriceStr ? `<div class="date-price-under">${firstPriceStr}</div>` : ""}
          <div id="dates-dd-${it.slug}" class="dates-dd-menu hidden">
            ${evs
              .map(
                (ev, i) => {
                  const pStr = fmtPricePerTicket(ev);
                  return `
              <button type="button" class="dates-dd-item ${i === 0 && ev.has_tickets !== false && !ev.other_away_label ? "first-avail" : ""} ${ev.other_away_label ? "away-opt" : ""}"
                onclick="selectDateFromDropdown('${it.slug}', ${ev.event_id}, event)">
                <span>
                  <span class="dd-item-date">${ev.weekday}, ${ev.label} · ${ev.event_time}</span>
                  ${pStr ? `<span class="dd-item-price">${pStr}</span>` : ""}
                </span>
                <span class="dd-item-tag">${
                  ev.other_away_label
                    ? ev.other_away_label
                    : ev.has_tickets === false
                    ? "скоро старт"
                    : i === 0
                    ? "ближайшая ›"
                    : "выбрать ›"
                }</span>
              </button>
            `;
                }
              )
              .join("")}
          </div>
        </div>
      `;
    }
  }

  const gmIconHtml = it.is_golden_mask ? `<span class="gm-inline-icon" title="Золотая Маска">${iconGoldenMask(16)}</span>` : "";

  let badgesHtml = "";
  if (it.source_type === "director_radar" && it.discovered_by_director) {
    badgesHtml += `<span class="partner-match-tag">✦ Найдено по автору: ${it.discovered_by_director}</span>`;
  }
  if (it.partner_wants) {
    badgesHtml += `<span class="partner-match-tag">♥ ${it.partner_name} тоже хочет</span>`;
  }
  if (it.both_seen) {
    badgesHtml += `<span class="mini-status-pill" style="color:var(--green);">✓ Были оба: А и +1</span>`;
  } else if (state.repFilter !== "inbox" && it.status !== "inbox") {
    const stColor = isWant ? "color:#58D68D;" : it.status === "skip" ? "color:#FF6B63;" : "color:#EDE6DA;";
    const stText = isWant ? '♥ "Хочу"' : it.status === "skip" ? '✕ "Не хочу"' : '✓ "Уже был"';
    badgesHtml += `<span class="mini-status-pill" style="${stColor}">${stText}</span>`;
  }

  return `
    <article class="feed-swipe-card" id="prod-card-${it.slug}" data-slug="${it.slug}">
      <div class="swipe-stamp want">"ХОЧУ" →</div>
      <div class="swipe-stamp skip">← "НЕ ХОЧУ"</div>
      <div class="swipe-stamp seen">"УЖЕ БЫЛ"</div>

      <div class="card-top-grid">
        <div class="card-top-left">
          <div class="director-tag">${it.venue_name}</div>
          <h3 class="card-title">${it.title}</h3>
        </div>
        ${datesCornerHtml}
      </div>

      <div class="creator-line"><span class="creator-role-lbl">${roleLabel}:</span> ${creatorName}${!it.note && gmIconHtml ? gmIconHtml : ""}</div>
      ${it.duration ? `<div class="venue-line" style="margin-top:2px;">${it.duration}</div>` : ""}

      ${it.note ? `<div class="venue-line" style="margin-top:7px;line-height:1.38;color:#C8C1B6;">${it.note}${gmIconHtml}</div>` : ""}
      ${badgesHtml}
    </article>
  `;
}

window.toggleDatesDropdown = (slug, ev) => {
  if (ev) ev.stopPropagation();
  const target = document.getElementById(`dates-dd-${slug}`);
  const parentCard = document.getElementById(`prod-card-${slug}`);
  const wasOpen = target && !target.classList.contains("hidden");
  document.querySelectorAll(".dates-dd-menu").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".feed-swipe-card.dd-open").forEach((el) => el.classList.remove("dd-open"));
  if (target && !wasOpen) {
    target.classList.remove("hidden");
    if (parentCard) parentCard.classList.add("dd-open");
  }
};

window.selectDateFromDropdown = (slug, eventId, ev) => {
  if (ev) ev.stopPropagation();
  document.querySelectorAll(".dates-dd-menu").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".feed-swipe-card.dd-open").forEach((el) => el.classList.remove("dd-open"));
  openDateEventSheet(slug, eventId);
};

document.addEventListener("click", (e) => {
  if (!e.target.closest(".dates-dd-wrap")) {
    document.querySelectorAll(".dates-dd-menu").forEach((el) => el.classList.add("hidden"));
    document.querySelectorAll(".feed-swipe-card.dd-open").forEach((el) => el.classList.remove("dd-open"));
  }
});

window.setRepFilter = (f) => {
  state.repFilter = f;
  renderAll();
};

window.setDirectorFilter = (dir) => {
  state.directorFilter = dir;
  renderAll();
};

window.openDateEventSheet = (slug, eventId) => {
  const prod = (state.data.repertoire || []).find((p) => p.slug === slug);
  if (!prod) return;
  const ev = (prod.upcoming_events || []).find((e) => e.event_id === eventId) || prod.upcoming_events[0];
  if (!ev) return;

  const comps = (state.data && state.data.companions) || [];
  const sel = getEventSel({ event_id: ev.event_id, party_ids: prod.party_ids });
  const topSeat = (prod.seat_targets && prod.seat_targets[0]) || {};
  const total = (ev.est_ticket_price || 8000) * sel.count;

  const pTicketStr = fmtPricePerTicket(ev);
  openSheet(`
    <div class="when-bar">
      <div>
        <span class="when-time">${ev.event_time}</span>
        ${ev.end_time ? `<span class="when-end-sub" style="margin-left:5px;">до ~${ev.end_time}</span>` : ""}
      </div>
      <div style="text-align:right;">
        <div class="when-date-str">${ev.weekday_full}, ${ev.full_date}</div>
        ${pTicketStr ? `<div class="date-price-under">${pTicketStr}</div>` : ""}
      </div>
    </div>

    ${
      ev.is_primary_away
        ? `<div class="away-day-card" style="margin-bottom:10px;">
            <div style="font-size:12.5px;font-weight:700;color:#F8E2E8;">● На эту дату отмечено «Нет в Мск»</div>
          </div>`
        : ""
    }

    <div class="director-tag">${prod.venue_name}</div>
    <h3 style="margin:3px 0 4px;font-size:19px;">${prod.title}</h3>
    <div class="venue-line">${prod.stage_director || prod.director}${prod.duration ? " · " + prod.duration : ""}</div>

    <div class="seat-box" style="cursor:default;">
      <div>
        <div class="seat-box-main">Лучшее место (#1): ${topSeat.zone || "Ряд после прохода"}, ряд ${topSeat.row || "1"}</div>
        <div class="seat-box-why">${topSeat.why || prod.hall_summary}</div>
      </div>
    </div>

    ${renderCompactPartyBar(ev.event_id, sel, comps, slug)}

    <div class="card-actions" style="margin-top:12px;">
      <div class="price-block">
        <span class="price-main">~${fmtRub(total)}</span>
        <span class="price-sub">${sel.count} × ${pTicketStr || fmtRub(ev.est_ticket_price || 8000)}</span>
      </div>
      <button type="button" class="btn-primary" onclick="buyFromDateSheet('${slug}', ${ev.event_id})">
        Взять места
      </button>
    </div>

    <button type="button" class="btn-ghost" style="width:100%;margin-top:10px;" onclick="jumpToDateInSchedule('${ev.event_date}')">
      Показать этот день в расписании →
    </button>
  `);
};

window.setSheetTicketQty = (slug, eventId, qty) => {
  setTicketQty(eventId, qty);
  openDateEventSheet(slug, eventId);
};

window.toggleSheetPerson = (slug, eventId, personId) => {
  toggleEventPerson(eventId, personId);
  openDateEventSheet(slug, eventId);
};

window.buyFromDateSheet = async (slug, eventId) => {
  await fetch(`/api/productions/${slug}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: "sniper", person_name: state.activePerson }),
  });
  await startCheckout(eventId);
};

window.jumpToDateInSchedule = (isoDate) => {
  closeSheet();
  const weeks = (state.data.proposals && state.data.proposals.weeks) || [];
  const foundIdx = weeks.findIndex((w) => w.start_date <= isoDate && isoDate <= w.end_date);
  if (foundIdx >= 0) state.weekIndex = foundIdx;
  switchTab("week");
  setTimeout(() => scrollToDay(isoDate), 80);
};

const RU_MONTH_TITLE = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];
const RU_MONTH_GEN_JS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

function fmtHumanRangeJs(sIso, eIso) {
  if (!sIso) return "Проведите пальцем по датам";
  const fmt = (iso) => {
    const [y, m, d] = iso.split("-").map(Number);
    return `${d} ${RU_MONTH_GEN_JS[(m || 1) - 1]}`;
  };
  const a = sIso <= eIso ? sIso : eIso;
  const b = sIso <= eIso ? eIso : sIso;
  return a === b ? fmt(a) : `${fmt(a)} – ${fmt(b)}`;
}

function buildMonthGridHtml(year, month0, todayIso, existingAwaySet) {
  const firstDay = new Date(year, month0, 1);
  // Пн=0 .. Вс=6
  const startOffset = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month0 + 1, 0).getDate();
  const cells = [];

  for (let i = 0; i < startOffset; i++) {
    cells.push(`<div class="cal-day empty"></div>`);
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const mm = String(month0 + 1).padStart(2, "0");
    const dd = String(d).padStart(2, "0");
    const iso = `${year}-${mm}-${dd}`;
    const isPast = todayIso && iso < todayIso;
    const hasExisting = existingAwaySet.has(iso);
    cells.push(
      `<div class="cal-day ${isPast ? "past" : ""} ${hasExisting ? "existing-away" : ""}" data-iso="${iso}">${d}</div>`
    );
  }

  return `
    <div class="away-cal-month">
      <div class="away-cal-month-head">
        <span>${RU_MONTH_TITLE[month0]} ${year}</span>
      </div>
      <div class="away-cal-weekdays">
        <span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span class="wknd">Сб</span><span class="wknd">Вс</span>
      </div>
      <div class="away-cal-grid">
        ${cells.join("")}
      </div>
    </div>
  `;
}

function getExistingAwaySetForPerson(personName) {
  const set = new Set();
  const periods = (state.data && state.data.away_periods) || [];
  periods
    .filter((p) => p.person_name === personName)
    .forEach((p) => {
      const cur = new Date(p.start_date + "T12:00:00");
      const end = new Date(p.end_date + "T12:00:00");
      while (cur <= end) {
        const y = cur.getFullYear();
        const m = String(cur.getMonth() + 1).padStart(2, "0");
        const d = String(cur.getDate()).padStart(2, "0");
        set.add(`${y}-${m}-${d}`);
        cur.setDate(cur.getDate() + 1);
      }
    });
  return set;
}

function findMatchingSavedAway(personName, minIso, maxIso) {
  if (!minIso || !maxIso) return [];
  const periods = (state.data && state.data.away_periods) || [];
  return periods.filter(
    (p) => p.person_name === personName && p.start_date <= maxIso && p.end_date >= minIso
  );
}

function renderAwaySavedList() {
  const box = document.getElementById("awaySavedListWrap");
  if (!box) return;
  const allAway = sortAwayAFirst((state.data && state.data.away_periods) || []);
  if (!allAway.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">Уже отмечено (нажмите ✕, чтобы удалить):</div>
    <div class="away-saved-list">
      ${allAway
        .map(
          (a) => `
        <span class="away-saved-chip" style="${a.person_name === awayCalState.person ? "border-color:var(--rose);" : "opacity:0.78;"}">
          <span>${a.person_name}: ${fmtShortRange(a.start_date, a.end_date)}</span>
          <button type="button" class="away-saved-del" onclick="deleteAwayFromSheet(${a.id})">Удалить ✕</button>
        </span>
      `
        )
        .join("")}
    </div>
  `;
}

function paintAwayCalSelection() {
  const wrap = document.getElementById("awayCalWrap");
  if (!wrap) return;
  const s = awayCalState.startIso;
  const e = awayCalState.endIso || s;
  const minIso = s && e ? (s <= e ? s : e) : "";
  const maxIso = s && e ? (s <= e ? e : s) : "";

  wrap.querySelectorAll(".cal-day[data-iso]").forEach((el) => {
    const iso = el.dataset.iso;
    const inRange = Boolean(minIso && iso >= minIso && iso <= maxIso);
    el.classList.toggle("in-range", inRange);
    el.classList.toggle("range-start", Boolean(minIso && iso === minIso));
    el.classList.toggle("range-end", Boolean(maxIso && iso === maxIso));
  });

  const lbl = document.getElementById("awayRangePreview");
  if (lbl) {
    lbl.textContent = minIso
      ? `${awayCalState.person}: ${fmtHumanRangeJs(minIso, maxIso)}`
      : `${awayCalState.person}: проведите пальцем по датам`;
  }

  const delBtn = document.getElementById("awayDeleteOverlapBtn");
  if (delBtn) {
    const overlaps = findMatchingSavedAway(awayCalState.person, minIso, maxIso);
    if (overlaps.length > 0) {
      delBtn.classList.remove("hidden");
      delBtn.textContent = `Удалить отъезд (${fmtShortRange(overlaps[0].start_date, overlaps[0].end_date)})`;
      delBtn.onclick = () => deleteOverlappingAway(overlaps.map((o) => o.id));
    } else {
      delBtn.classList.add("hidden");
    }
  }
}

function rerenderAwayCalMonths() {
  const stack = document.getElementById("awayCalStack");
  if (!stack) return;
  const todayIso = (state.data && state.data.proposals && state.data.proposals.today_iso) || "2026-11-24";
  const existingSet = getExistingAwaySetForPerson(awayCalState.person);

  const y1 = awayCalState.baseYear;
  const m1 = awayCalState.baseMonth;
  const d2 = new Date(y1, m1 + 1, 1);
  const y2 = d2.getFullYear();
  const m2 = d2.getMonth();

  renderAwaySavedList();
  stack.innerHTML =
    buildMonthGridHtml(y1, m1, todayIso, existingSet) + buildMonthGridHtml(y2, m2, todayIso, existingSet);
  paintAwayCalSelection();
}

window.shiftAwayCalMonth = (delta) => {
  const d = new Date(awayCalState.baseYear, awayCalState.baseMonth + delta, 1);
  awayCalState.baseYear = d.getFullYear();
  awayCalState.baseMonth = d.getMonth();
  rerenderAwayCalMonths();
};

function bindAwayCalendarDrag() {
  const wrap = document.getElementById("awayCalWrap");
  if (!wrap || wrap.dataset.bound === "1") return;
  wrap.dataset.bound = "1";

  const hitIsoAt = (clientX, clientY) => {
    const el = document.elementFromPoint(clientX, clientY);
    const cell = el && el.closest ? el.closest(".cal-day[data-iso]") : null;
    return cell ? cell.dataset.iso : null;
  };

  wrap.addEventListener(
    "touchstart",
    (e) => {
      if (!e.touches.length) return;
      const cell = e.target.closest(".cal-day[data-iso]");
      if (!cell) return;
      awayCalState.isDragging = true;
      awayCalState.startIso = cell.dataset.iso;
      awayCalState.endIso = cell.dataset.iso;
      paintAwayCalSelection();
    },
    { passive: true }
  );

  wrap.addEventListener(
    "touchmove",
    (e) => {
      if (!awayCalState.isDragging || !e.touches.length) return;
      e.preventDefault();
      const iso = hitIsoAt(e.touches[0].clientX, e.touches[0].clientY);
      if (iso && iso !== awayCalState.endIso) {
        awayCalState.endIso = iso;
        paintAwayCalSelection();
      }
    },
    { passive: false }
  );

  wrap.addEventListener("touchend", () => {
    awayCalState.isDragging = false;
  });

  wrap.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;
    const cell = e.target.closest(".cal-day[data-iso]");
    if (!cell) return;
    try {
      wrap.setPointerCapture(e.pointerId);
    } catch (_) {}
    awayCalState.isDragging = true;
    awayCalState.startIso = cell.dataset.iso;
    awayCalState.endIso = cell.dataset.iso;
    paintAwayCalSelection();
  });

  wrap.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch" || !awayCalState.isDragging) return;
    const iso = hitIsoAt(e.clientX, e.clientY);
    if (iso && iso !== awayCalState.endIso) {
      awayCalState.endIso = iso;
      paintAwayCalSelection();
    }
  });

  wrap.addEventListener("pointerup", (e) => {
    if (e.pointerType === "touch") return;
    awayCalState.isDragging = false;
  });
}

window.openAwaySheet = (prefillDate = "", prefillPerson = "") => {
  const todayIso = (state.data && state.data.proposals && state.data.proposals.today_iso) || "2026-11-24";
  const anchorIso = prefillDate || todayIso;
  const [ay, am] = anchorIso.split("-").map(Number);

  awayCalState.person = prefillPerson === "+1" || (!prefillPerson && state.activePerson === "+1") ? "+1" : "А";
  awayCalState.baseYear = ay || 2026;
  awayCalState.baseMonth = (am || 11) - 1;
  awayCalState.startIso = prefillDate || "";
  awayCalState.endIso = prefillDate || "";
  awayCalState.isDragging = false;

  openSheet(`
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px;">
      <div>
        <h3 style="margin:0;font-size:18px;">Нет в Мск</h3>
        <div class="venue-line" style="font-size:11.5px;margin-top:2px;">Проведите пальцем от начала до конца</div>
      </div>
      <div class="person-slider wide" id="awayPersonSlider" data-person="${awayCalState.person}" style="flex-shrink:0;">
        <div class="person-slider-thumb"></div>
        <button type="button" class="person-slider-opt ${awayCalState.person === "А" ? "active" : ""}" data-val="А">А</button>
        <button type="button" class="person-slider-opt ${awayCalState.person === "+1" ? "active" : ""}" data-val="+1">+1</button>
      </div>
    </div>

    <div id="awaySavedListWrap"></div>

    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
      <span id="awayRangePreview" style="font-size:12.5px;font-weight:700;color:var(--rose);"></span>
      <div style="display:flex;gap:5px;">
        <button type="button" class="pill-btn" style="padding:3px 9px;" onclick="shiftAwayCalMonth(-1)">‹</button>
        <button type="button" class="pill-btn" style="padding:3px 9px;" onclick="shiftAwayCalMonth(1)">›</button>
      </div>
    </div>

    <div id="awayCalWrap" class="away-cal-wrap">
      <div id="awayCalStack" class="away-cal-months-stack"></div>
    </div>

    <div style="display:flex;gap:8px;">
      <button type="button" id="awayDeleteOverlapBtn" class="btn-ghost hidden" style="color:var(--danger);border-color:rgba(232,107,100,0.45);padding:11px 12px;font-size:13px;" onclick="">Удалить</button>
      <button type="button" class="btn-primary" style="flex:1;padding:12px;font-size:15px;" onclick="saveAwayPeriod()">ОК</button>
    </div>
  `);

  const awaySlider = document.getElementById("awayPersonSlider");
  bindPersonSlider(
    awaySlider,
    () => awayCalState.person,
    (chosen) => {
      awayCalState.person = chosen;
      awayCalState.startIso = "";
      awayCalState.endIso = "";
      rerenderAwayCalMonths();
    }
  );
  syncSliderVisual(awaySlider, awayCalState.person);
  rerenderAwayCalMonths();
  bindAwayCalendarDrag();
};

window.deleteAwayFromSheet = async (periodId) => {
  await fetch(`/api/away-periods/${periodId}`, { method: "DELETE" });
  awayCalState.startIso = "";
  awayCalState.endIso = "";
  await loadState();
  rerenderAwayCalMonths();
  showToast("Ранее введённые даты удалены");
};

window.deleteOverlappingAway = async (ids) => {
  for (const id of ids) {
    await fetch(`/api/away-periods/${id}`, { method: "DELETE" });
  }
  awayCalState.startIso = "";
  awayCalState.endIso = "";
  await loadState();
  rerenderAwayCalMonths();
  showToast("Ранее введённые даты удалены");
};

window.saveAwayPeriod = async () => {
  const s = awayCalState.startIso;
  const e = awayCalState.endIso || s;
  if (!s) {
    closeSheet();
    return;
  }
  const startDate = s <= e ? s : e;
  const endDate = s <= e ? e : s;
  const personName = awayCalState.person;

  await fetch("/api/away-periods", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      person_name: personName,
      is_primary: personName === "А",
      start_date: startDate,
      end_date: endDate,
      note: "Нет в Мск",
    }),
  });
  closeSheet();
  await loadState();
  showToast(`Отмечено для ${personName}: ${fmtHumanRangeJs(startDate, endDate)}`);
};

window.deleteAwayPeriod = async (periodId) => {
  await fetch(`/api/away-periods/${periodId}`, { method: "DELETE" });
  await loadState();
  showToast("Отметка отъезда убрана");
};

function bindSwipePhysics() {
  document.querySelectorAll(".feed-swipe-card").forEach((card) => {
    const slug = card.dataset.slug;
    const stampWant = card.querySelector(".swipe-stamp.want");
    const stampSkip = card.querySelector(".swipe-stamp.skip");
    const stampSeen = card.querySelector(".swipe-stamp.seen");

    let startX = 0;
    let startY = 0;
    let startTime = 0;
    let dx = 0;
    let dy = 0;
    let tracking = false;
    let lockedAxis = null; // 'x' | 'scroll' | null
    let lastTapAt = 0;

    const fireDoubleTapSeen = () => {
      if (card.dataset.decided === "1") return;
      card.dataset.decided = "1";
      if (stampSeen) stampSeen.style.opacity = "1";
      triggerCardDecision(slug, "seen", 0);
    };

    const onStart = (x, y, target) => {
      if (card.dataset.decided === "1") return;
      if (target && target.closest("button, a, select, .dates-dd-wrap")) return;
      startX = x;
      startY = y;
      startTime = Date.now();
      dx = 0;
      dy = 0;
      tracking = true;
      lockedAxis = null;
      card.style.transition = "none";
    };

    const onMove = (x, y) => {
      if (!tracking) return;
      dx = x - startX;
      dy = y - startY;

      if (!lockedAxis) {
        if (Math.abs(dx) < 9 && Math.abs(dy) < 9) return;
        if (Math.abs(dx) > Math.abs(dy) * 1.15) {
          lockedAxis = "x";
        } else {
          lockedAxis = "scroll";
        }
      }

      if (lockedAxis === "x") {
        const rot = dx * 0.035;
        card.style.transform = `translateX(${dx}px) rotate(${rot}deg)`;
        if (stampWant) stampWant.style.opacity = dx > 22 ? String(Math.min(1, (dx - 22) / 60)) : "0";
        if (stampSkip) stampSkip.style.opacity = dx < -22 ? String(Math.min(1, (-dx - 22) / 60)) : "0";
        if (stampSeen) stampSeen.style.opacity = "0";
      }
    };

    const onEnd = (isTouch = false) => {
      if (!tracking) return;
      tracking = false;
      const elapsed = Math.max(1, Date.now() - startTime);

      if (lockedAxis === "x" && dx > 75) {
        card.dataset.decided = "1";
        triggerCardDecision(slug, "sniper", 1);
      } else if (lockedAxis === "x" && dx < -75) {
        card.dataset.decided = "1";
        triggerCardDecision(slug, "skip", -1);
      } else {
        card.style.transition = "transform 0.22s cubic-bezier(0.2, 0.8, 0.2, 1)";
        card.style.transform = "translate(0px, 0px) rotate(0deg) scale(1)";
        if (stampWant) stampWant.style.opacity = "0";
        if (stampSkip) stampSkip.style.opacity = "0";
        if (stampSeen) stampSeen.style.opacity = "0";

        // Чистый короткий тап без движения пальца: проверяем двойное нажатие (2× тап = «Уже был»)
        if (isTouch && Math.abs(dx) < 12 && Math.abs(dy) < 12 && elapsed < 320) {
          const now = Date.now();
          if (now - lastTapAt < 360) {
            lastTapAt = 0;
            fireDoubleTapSeen();
            return;
          }
          lastTapAt = now;
        }
      }
      lockedAxis = null;
    };

    card.addEventListener("touchstart", (e) => onStart(e.touches[0].clientX, e.touches[0].clientY, e.target), { passive: true });
    card.addEventListener("touchmove", (e) => onMove(e.touches[0].clientX, e.touches[0].clientY), { passive: true });
    card.addEventListener("touchend", () => onEnd(true));

    card.addEventListener("dblclick", (e) => {
      if (e.target && e.target.closest("button, a, select, .dates-dd-wrap")) return;
      fireDoubleTapSeen();
    });

    card.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "touch") return;
      if (e.target && e.target.closest("button, a, select, .dates-dd-wrap")) return;
      try {
        card.setPointerCapture(e.pointerId);
      } catch (_) {}
      onStart(e.clientX, e.clientY, e.target);
    });
    card.addEventListener("pointermove", (e) => {
      if (e.pointerType === "touch") return;
      onMove(e.clientX, e.clientY);
    });
    card.addEventListener("pointerup", (e) => {
      if (e.pointerType === "touch") return;
      onEnd(false);
    });
  });
}

window.triggerCardDecision = async (slug, status, dir) => {
  const card = document.getElementById(`prod-card-${slug}`);
  if (card) {
    card.style.transition = "transform 0.2s ease-out, opacity 0.2s ease-out";
    if (dir > 0) card.style.transform = "translateX(380px) rotate(10deg)";
    else if (dir < 0) card.style.transform = "translateX(-380px) rotate(-10deg)";
    else card.style.transform = "scale(0.94)";
    card.style.opacity = "0";
    await new Promise((r) => setTimeout(r, 170));
  }

  if (!state.swipeHistoryByPerson[state.activePerson]) {
    state.swipeHistoryByPerson[state.activePerson] = [];
  }
  state.swipeHistoryByPerson[state.activePerson].push(slug);

  const swipedProd = (state.data && state.data.repertoire || []).find((p) => p.slug === slug);

  await fetch(`/api/productions/${slug}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, person_name: state.activePerson }),
  });

  const res = await fetch(`/api/state?person=${encodeURIComponent(state.activePerson)}`);
  state.data = await res.json();

  const remaining = (state.data.repertoire || []).filter((p) => p.status === "inbox").length;
  if (state.repFilter === "inbox" && remaining === 0) {
    showToast("Все спектакли разобраны — открываю ваше расписание!");
    switchTab("week");
  } else {
    renderAll();
  }

  if (status === "seen" && swipedProd) {
    openPostShowSheet({
      item_key: `prod:${slug}`,
      production_slug: slug,
      title: swipedProd.title,
      venue_name: swipedProd.venue_name,
      stage_director: swipedProd.stage_director || swipedProd.director || "",
      event_date: "",
    });
  }
};

window.undoLastSwipe = async () => {
  const hist = state.swipeHistoryByPerson[state.activePerson] || [];
  const slug = hist.pop();
  if (!slug) return;
  await fetch(`/api/productions/${slug}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: "inbox", person_name: state.activePerson }),
  });
  await loadState();
  showToast("Последний спектакль возвращён");
};

/* ================= ВКЛАДКА 2: РАСПИСАНИЕ ПО ДНЯМ (УЖЕ ЕСТЬ vs МОЖЕТ БЫТЬ) ================= */
function iconWhiteHeart(size = 38) {
  return `<svg class="verdict-svg" style="width:${size}px;height:${size}px;" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path fill="#FFFFFF" d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>`;
}

function iconWhiteSkull(size = 38) {
  return `<svg class="verdict-svg" style="width:${size}px;height:${size}px;" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">
    <path fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" d="M3.8 15.7L20.2 21.3M20.2 15.7L3.8 21.3"/>
    <circle cx="3.4" cy="15.4" r="1.35" fill="#FFFFFF"/>
    <circle cx="20.6" cy="15.4" r="1.35" fill="#FFFFFF"/>
    <circle cx="3.4" cy="21.5" r="1.35" fill="#FFFFFF"/>
    <circle cx="20.6" cy="21.5" r="1.35" fill="#FFFFFF"/>
    <path fill="#FFFFFF" d="M12 1.7c-3.55 0-6.25 2.52-6.25 5.82 0 2.02 1.02 3.72 2.62 4.65v1.68c0 .72.58 1.3 1.3 1.3h4.66c.72 0 1.3-.58 1.3-1.3v-1.68c1.6-.93 2.62-2.63 2.62-4.65 0-3.3-2.7-5.82-6.25-5.82z"/>
    <circle cx="9.75" cy="7.45" r="1.45" fill="#16151C"/>
    <circle cx="14.25" cy="7.45" r="1.45" fill="#16151C"/>
    <path fill="#16151C" d="M12 8.9l-1.1 1.85h2.2z"/>
    <path stroke="#16151C" stroke-width="1.15" stroke-linecap="round" d="M10.75 12.65v1.9M13.25 12.65v1.9"/>
  </svg>`;
}

function renderBookedTicketCard(t) {
  const hasFile = Boolean(t.filename);
  const todayIso = (state.data && state.data.proposals && state.data.proposals.today_iso) || "2026-11-24";
  const isPast = Boolean(t.event_date && t.event_date < todayIso);
  const reviewedMap = (state.data && state.data.post_show && state.data.post_show.reviewed) || {};
  const verdict = reviewedMap[`ticket:${t.id}`] || "";

  let reviewFooter = "";
  if (isPast) {
    if (verdict === "liked") {
      reviewFooter = `<div style="margin-top:8px;"><span class="badge verified" style="display:inline-flex;align-items:center;gap:5px;">${iconWhiteHeart(14)} Понравилось · в «Нравятся»</span></div>`;
    } else if (verdict === "disliked") {
      reviewFooter = `<div style="margin-top:8px;"><span class="badge" style="display:inline-flex;align-items:center;gap:5px;">${iconWhiteSkull(14)} Не понравилось</span></div>`;
    } else {
      const payload = JSON.stringify({
        item_key: `ticket:${t.id}`,
        title: t.title,
        venue_name: t.venue_name || "",
        event_date: t.event_date || "",
      }).replace(/'/g, "&#39;");
      reviewFooter = `
        <div style="margin-top:10px;padding-top:9px;border-top:1px solid rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <span style="font-size:12px;font-weight:700;color:var(--gold);">Понравился спектакль?</span>
          <div style="display:flex;gap:6px;">
            <button type="button" class="verdict-btn compact-row like" onclick='submitPostShowReview(${payload}, "liked")'>${iconWhiteHeart(22)} Понравилось</button>
            <button type="button" class="verdict-btn compact-row dislike" onclick='submitPostShowReview(${payload}, "disliked")'>${iconWhiteSkull(22)} Не понравилось</button>
          </div>
        </div>
      `;
    }
  }

  return `
    <div class="booked-card">
      <div class="when-bar" style="border-color:rgba(88,194,139,0.25);">
        <span class="when-time" style="background:var(--green-soft);color:var(--green);">${isPast ? "✓ ПРОШЁЛ" : "🎟 УЖЕ ЕСТЬ"} · ${t.event_time}</span>
        <div class="when-date-str">${t.weekday_full}, ${t.full_date}</div>
      </div>
      <h3 class="card-title">${t.title}</h3>
      <div class="venue-line" style="color:#D8E8DF;">${t.venue_name}</div>
      <div class="venue-line" style="margin-top:5px;color:var(--text);font-weight:600;">${t.seats_info || "Места в бланке"} · Идут: ${t.who_goes}</div>
      ${reviewFooter}
      <div style="margin-top:10px;display:flex;gap:8px;justify-content:space-between;align-items:center;">
        <span style="font-size:11px;color:var(--muted);">${t.sender_name ? `Добавил(а): ${t.sender_name}` : ""}</span>
        <div style="display:flex;gap:6px;">
          ${hasFile ? `<a class="btn-primary" style="background:var(--green);text-decoration:none;padding:8px 12px;font-size:12.5px;" href="/media/tickets/${t.filename}" target="_blank">Открыть билет ↗</a>` : ""}
          <button type="button" class="btn-ghost" style="padding:7px 10px;font-size:12px;" onclick="deleteWalletTicket(${t.id})">✕</button>
        </div>
      </div>
    </div>
  `;
}

function renderOfferCard(ev) {
  const comps = (state.data && state.data.companions) || [];
  const sel = getEventSel(ev);
  const total = ev.est_ticket_price * sel.count;
  const topTarget = (ev.seat_targets && ev.seat_targets[0]) || {};
  const pTicketStr = fmtPricePerTicket(ev);

  return `
    <article class="card">
      <div class="when-bar">
        <div>
          <span class="when-time">МОЖЕТ БЫТЬ · ${ev.event_time}</span>
          ${ev.end_time ? `<span class="when-end-sub" style="margin-left:5px;">до ~${ev.end_time}</span>` : ""}
        </div>
        <div style="text-align:right;">
          <div class="when-date-str">${ev.weekday_full}, ${ev.full_date}</div>
          ${pTicketStr ? `<div class="date-price-under">${pTicketStr}</div>` : ""}
        </div>
      </div>

      <div class="director-tag">${ev.venue_name}</div>
      <h3 class="card-title">${ev.title}</h3>
      <div class="creator-line"><span class="creator-role-lbl">${ev.director_role || (ev.category === "ballet" ? "Хореограф" : "Режиссёр")}:</span> ${ev.stage_director || ev.director}</div>
      ${ev.duration ? `<div class="venue-line" style="margin-top:2px;">${ev.duration}</div>` : ""}

      <div class="seat-box" onclick='showHallSheet(${JSON.stringify(ev.production_slug)})'>
        <div>
          <div class="seat-box-main">Лучшее место: ${topTarget.zone || "Ряд после прохода"}, ряд ${topTarget.row || "1"}</div>
          <div class="seat-box-why">${topTarget.why || ev.hall_summary}</div>
        </div>
        <span style="color:var(--gold);font-weight:700;">›</span>
      </div>

      ${renderCompactPartyBar(ev.event_id, sel, comps)}

      <div class="card-actions" style="margin-top:9px;">
        <div class="price-block">
          <span class="price-main">~${fmtRub(total)}</span>
          <span class="price-sub">${sel.count} × ${pTicketStr || fmtRub(ev.est_ticket_price)} в офиц. кассе</span>
        </div>
        <button type="button" class="btn-primary" onclick="startCheckout(${ev.event_id})">
          Взять места
        </button>
      </div>
    </article>
  `;
}

function renderWeekTab() {
  const p = state.data.proposals || {};
  const weeks = p.weeks || [];
  if (!weeks.length) return `<div class="card">Нет данных расписания</div>`;

  const wk = weeks[Math.min(state.weekIndex, weeks.length - 1)];

  return `
    ${renderAwayTopBar()}

    ${
      p.swiped_want_count === 0 && p.inbox_count > 0
        ? `
      <div class="card" style="border-color:rgba(212,175,106,0.5);display:flex;justify-content:space-between;align-items:center;gap:10px;">
        <div class="venue-line" style="color:var(--text);">
          В отборе <b>${p.inbox_count} спектаклей</b> — смахните нужные вправо («Хочу →»), и они появятся здесь по дням!
        </div>
        <button type="button" class="btn-primary" style="flex-shrink:0;" onclick="switchTab('swipe')">К отбору →</button>
      </div>
    `
        : ""
    }

    <div class="week-switcher">
      <div class="week-nav-row">
        <button type="button" class="week-arrow" ${wk.week_index === 0 ? "disabled" : ""} onclick="changeWeek(-1)">‹</button>
        <div class="week-center-title">
          <div class="week-badge-top">${wk.week_badge}</div>
          <div class="week-dates-main">${wk.range_title}</div>
        </div>
        <button type="button" class="week-arrow" ${wk.week_index >= weeks.length - 1 ? "disabled" : ""} onclick="changeWeek(1)">›</button>
      </div>

      <div class="week-strip">
        ${wk.days
          .map((d) => {
            const hasBooked = d.booked_tickets.length > 0;
            const hasOffers = d.offers.length > 0;
            const hasAway = (d.away_items || []).length > 0;
            return `
            <button type="button" class="day-cell ${d.is_past ? "past" : ""} ${hasBooked ? "booked" : hasAway ? "away" : ""} ${hasOffers ? "has-maybe" : ""}"
              onclick="openDayManualEventSheet('${d.iso_date}')">
              <div class="day-cell-wd ${d.is_weekend ? "weekend" : ""}">${d.weekday_short}</div>
              <div class="day-cell-num">${d.day_num}</div>
              <div class="day-cell-dots">
                ${hasBooked ? `<span class="dot-booked"></span>` : ""}
                ${hasAway ? `<span class="dot-away"></span>` : ""}
                ${hasOffers ? `<span class="dot-offer"></span>` : ""}
              </div>
            </button>
          `;
          })
          .join("")}
      </div>

      <div class="legend-row">
        <span class="legend-item"><span class="dot-booked"></span> Уже есть (${wk.total_booked})</span>
        <span class="legend-item"><span class="dot-offer"></span> Может быть (${wk.total_offers})</span>
        <span class="legend-item"><span class="dot-away"></span> Нет в Мск (${wk.total_away || 0})</span>
      </div>
    </div>

    ${wk.days
      .filter((d) => !d.is_past || d.booked_tickets.length > 0)
      .map((d) => {
        const isBusy = d.booked_tickets.length > 0;
        const awayItems = sortAwayAFirst(d.away_items || []);
        const isPrimaryAway = Boolean(d.is_primary_away);
        const hasMaybe = d.offers.length > 0;
        const showHiddenOffers = Boolean(state.expandedBusyDates[d.iso_date]);

        return `
        <section id="day-${d.iso_date}" class="day-section ${awayItems.length ? "away-day-section" : ""}">
          <div class="day-header" style="${awayItems.length ? "border-bottom-color:rgba(217,138,158,0.32);" : ""}">
            <div class="day-header-left tappable-date" role="button" tabindex="0" onclick="openDayManualEventSheet('${d.iso_date}')">
              <span class="day-weekday ${d.is_weekend ? "weekend" : ""}">${d.weekday_full}</span>
              <span class="day-full-date">${d.full_date}</span>
              <span class="day-plus-hint">+ план</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
              ${
                !awayItems.length
                  ? `<button type="button" class="away-mini-day-btn" onclick="openAwaySheet('${d.iso_date}')">Нет в Мск</button>`
                  : ""
              }
              <span class="day-rel-tag ${isBusy ? "busy" : isPrimaryAway ? "away" : hasMaybe ? "maybe" : ""}">
                ${isBusy ? "🎟 Уже есть билет" : isPrimaryAway ? "Нет в Мск" : hasMaybe ? `Может быть: ${d.offers.length}` : d.relative}
              </span>
            </div>
          </div>

          ${awayItems
            .map(
              (a) => `
            <div class="away-day-card">
              <div>
                <div class="away-day-title">${a.person_name}: Нет в Мск</div>
                <div class="away-day-sub">Автоснайпер для ${a.person_name} на этот день остановлен</div>
              </div>
              <button type="button" class="away-red-btn" onclick="deleteAwayPeriod(${a.id})">Убрать ✕</button>
            </div>
          `
            )
            .join("")}

          ${d.booked_tickets.map(renderBookedTicketCard).join("")}

          ${
            isPrimaryAway
              ? ""
              : !isBusy && hasMaybe
              ? d.offers.map(renderOfferCard).join("")
              : isBusy && hasMaybe
              ? `
                ${
                  showHiddenOffers
                    ? d.offers.map(renderOfferCard).join("")
                    : `<button type="button" class="btn-ghost" style="width:100%;font-size:12px;color:var(--muted);" onclick="toggleBusyDayOffers('${d.iso_date}')">
                        На этот день может быть ещё ${d.offers.length} вар., но вечер уже занят · Показать
                      </button>`
                }
              `
              : !isBusy && !awayItems.length
              ? `<div class="empty-day-row" style="cursor:pointer;" onclick="openDayManualEventSheet('${d.iso_date}')">Свободный день — нажмите на дату, чтобы вписать время и мероприятие</div>`
              : ""
          }
        </section>
      `;
      })
      .join("")}
  `;
}

window.openDayManualEventSheet = (isoDate) => {
  const humanStr = fmtHumanRangeJs(isoDate, isoDate);
  openSheet(`
    <div class="director-tag">Ручная запись в расписание</div>
    <h3 style="margin:3px 0 10px;font-size:19px;">План на ${humanStr}</h3>

    <div style="display:grid;grid-template-columns:1.25fr 1fr;gap:8px;">
      <div class="form-group">
        <div class="form-label">Дата</div>
        <input id="dmDate" type="date" class="input-field" value="${isoDate}" />
      </div>
      <div class="form-group">
        <div class="form-label">Время</div>
        <input id="dmTime" type="time" class="input-field" value="19:00" />
      </div>
    </div>

    <div class="quick-time-chips" style="margin-bottom:10px;">
      ${["12:00", "15:00", "18:00", "19:00", "20:00"]
        .map(
          (tm) => `
        <button type="button" class="quick-chip-btn ${tm === "19:00" ? "active" : ""}" onclick="pickQuickEventTime('${tm}', this)">${tm}</button>
      `
        )
        .join("")}
    </div>

    <div class="form-group">
      <div class="form-label">Мероприятие (спектакль, выставка, ужин, планы)</div>
      <input id="dmTitle" class="input-field" placeholder="Например: Ужин / Выставка в ГЭС-2 / Спектакль" />
    </div>

    <div class="form-group">
      <div class="form-label">Площадка или места (необязательно)</div>
      <input id="dmVenue" class="input-field" placeholder="Например: Большая Дмитровка / Партер ряд 5" />
    </div>

    <div class="form-group" style="display:flex;align-items:center;justify-content:space-between;margin-top:4px;">
      <div class="form-label">Кто идёт / занят</div>
      <div class="person-pair-pill">
        <button type="button" class="person-pair-btn dm-who-chip active" data-who="А" onclick="toggleManualWhoChip(this)">А</button>
        <button type="button" class="person-pair-btn dm-who-chip active" data-who="+1" onclick="toggleManualWhoChip(this)">+1</button>
      </div>
    </div>

    <button type="button" class="btn-primary" style="width:100%;margin-top:8px;padding:12px;" onclick="saveDayManualEvent()">ОК — добавить в расписание</button>
    <button type="button" class="btn-ghost" style="width:100%;margin-top:8px;" onclick="closeSheet(); scrollToDay('${isoDate}')">Показать этот день в списке ↓</button>
  `);
  setTimeout(() => {
    const inp = document.getElementById("dmTitle");
    if (inp) inp.focus();
  }, 60);
};

window.pickQuickEventTime = (tm, btn) => {
  const inp = document.getElementById("dmTime");
  if (inp) inp.value = tm;
  document.querySelectorAll(".quick-time-chips .quick-chip-btn:not(.dm-who-chip)").forEach((b) => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
};

window.toggleManualWhoChip = (btn) => {
  const all = Array.from(document.querySelectorAll(".dm-who-chip"));
  const activeCount = all.filter((b) => b.classList.contains("active")).length;
  if (btn.classList.contains("active") && activeCount <= 1) return;
  btn.classList.toggle("active");
};

function readManualWhoSelection() {
  const chosen = Array.from(document.querySelectorAll(".dm-who-chip.active")).map((b) => b.dataset.who);
  return chosen.length ? chosen.join(", ") : "А, +1";
}

window.saveDayManualEvent = async () => {
  const title = (document.getElementById("dmTitle").value || "").trim();
  const event_date = document.getElementById("dmDate").value;
  const event_time = document.getElementById("dmTime").value || "19:00";
  const venue_name = (document.getElementById("dmVenue").value || "").trim();
  const who_goes = readManualWhoSelection();

  if (!title) return showToast("Введите название мероприятия");
  if (!event_date) return showToast("Укажите дату");

  await fetch("/api/wallet-tickets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      venue_name,
      event_date,
      event_time,
      seats_info: venue_name ? "" : "Записано вручную",
      who_goes,
    }),
  });
  closeSheet();
  await loadState();
  scrollToDay(event_date);
  showToast(`Добавлено на ${fmtHumanRangeJs(event_date, event_date)} в ${event_time}`);
};

window.setTicketQty = (eventId, qty) => {
  const comps = ((state.data && state.data.companions) || []).filter((c) => c.name === "А" || c.name === "+1");
  const cur = getEventSel({ event_id: eventId });
  cur.count = qty === 1 ? 1 : 2;
  if (cur.count === 2) {
    cur.ids = getDefaultAKIds(comps);
  } else {
    const activeComp =
      comps.find((c) => c.name === state.activePerson) ||
      comps.find((c) => c.is_self) ||
      comps[0];
    cur.ids = cur.ids.length === 1 ? cur.ids : activeComp ? [activeComp.id] : [];
  }
  state.eventSelection[eventId] = cur;
  renderAll();
};

window.toggleEventPerson = (eventId, personId) => {
  const cur = getEventSel({ event_id: eventId });
  const idx = cur.ids.indexOf(personId);
  if (idx >= 0) {
    if (cur.ids.length > 1) cur.ids.splice(idx, 1);
  } else {
    if (cur.ids.length >= 2) cur.ids.shift();
    cur.ids.push(personId);
  }
  cur.count = Math.max(1, Math.min(2, cur.ids.length));
  state.eventSelection[eventId] = cur;
  renderAll();
};

function renderMailInboxCard(s) {
  const addr = s.mail_address || "Lesteatres@mail.ru";
  const connected = Boolean(s.mail_connected);
  return `
    <div class="card" style="border-color:${connected ? "rgba(88,194,139,0.5)" : "rgba(212,175,106,0.5)"};">
      <div class="director-tag">Авточтение билетов с почты (каждые 3 мин)</div>
      <h3 class="card-title" style="margin-top:4px;font-size:17px;">✉ ${addr}</h3>
      <div class="venue-line" style="line-height:1.42;margin-top:4px;">
        Все электронные билеты и PDF-бланки касс читаются с этой почты и автоматически отмечаются как <b>«🎟 Уже есть»</b> в расписании.
      </div>
      <div style="margin-top:10px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
        <span class="badge ${connected ? "verified" : ""}" style="font-size:11px;">
          ${s.mail_status || "Ожидает пароль приложения Mail.ru"}
        </span>
        <div style="display:flex;gap:6px;">
          ${
            connected
              ? `<button type="button" class="btn-ghost" style="padding:7px 11px;font-size:12px;" onclick="checkMailNow(this)">Проверить</button>`
              : ""
          }
          <button type="button" class="btn-primary" style="padding:8px 12px;font-size:12.5px;" onclick="openMailSheet()">
            ${connected ? "Пароль" : "Подключить почту"}
          </button>
        </div>
      </div>
    </div>
  `;
}

function renderTicketsTab() {
  const wallet = state.data.wallet_tickets || [];
  const shots = state.data.screenshots || [];
  const pendingOrders = (state.data.orders || []).filter((o) => o.status === "awaiting_bank_push");
  const s = state.data.settings;

  return `
    ${renderMailInboxCard(s)}

    <div class="upload-drop">
      <div style="font-size:16px;font-weight:700;margin-bottom:5px;">Закинуть билет или скриншот с iPhone</div>
      <div class="venue-line" style="margin-bottom:12px;">
        PDF/QR купленного билета встанет как «Уже есть», а скриншот афиши попадёт в «Хочу».
      </div>
      <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">
        <label class="btn-primary" style="display:inline-block;">
          Загрузить билет (PDF/QR)
          <input type="file" accept=".pdf,image/*" multiple style="display:none" onchange="handleIncomingUpload(this, 'ticket')" />
        </label>
        <label class="btn-ghost" style="display:inline-block;">
          Скриншот «Хочу»
          <input type="file" accept="image/*" multiple style="display:none" onchange="handleIncomingUpload(this, 'wishlist')" />
        </label>
        <button type="button" class="btn-ghost" onclick="openManualTicketSheet()">Вписать вручную</button>
      </div>
      <div style="margin-top:11px;padding-top:9px;border-top:1px solid rgba(255,255,255,0.08);display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <span class="venue-line" style="font-size:12px;text-align:left;">
          Telegram-бот: <b style="color:var(--gold);">${s.tg_bot_username || "не подключён"}</b>
        </span>
        <button type="button" class="pill-btn" onclick="openTelegramSheet()">
          ${s.tg_bot_username ? "Настроить" : "Подключить"}
        </button>
      </div>
    </div>

    ${
      pendingOrders.length
        ? `
      <div class="section-head">
        <h2>Ждут пуша в банке (свайп ← отменит)</h2>
        <span>${pendingOrders.length}</span>
      </div>
      ${pendingOrders
        .map(
          (o) => `
        <div class="card pending-order-card" data-order-id="${o.id}">
          <div class="when-bar">
            <span class="when-time">До ${o.hold_expires_at}</span>
            <span class="when-date-str">${o.event_date} · ${o.event_time}</span>
          </div>
          <h3 class="card-title">${o.production_title}</h3>
          <div class="venue-line">${o.venue_name} · ${o.seat_selection}</div>
          <div class="venue-line" style="margin-top:6px;color:var(--gold);">${o.bank_hint}</div>
          <div class="card-actions">
            <button type="button" class="btn-ghost" onclick="cancelOrder(${o.id})">Отменить</button>
            <button type="button" class="btn-primary" onclick='openOrderConfirmSheet(${JSON.stringify(o)})'>Подтвердить / 3DS</button>
          </div>
        </div>
      `
        )
        .join("")}
    `
        : ""
    }

    <div class="section-head">
      <h2>Купленные билеты и планы («Уже есть»)</h2>
      <span>${wallet.length} шт.</span>
    </div>
    ${
      wallet.length
        ? wallet.map(renderBookedTicketCard).join("")
        : `<div class="card"><div class="venue-line">Пока нет добавленных билетов.</div></div>`
    }

    ${
      shots.length
        ? `
      <div class="section-head">
        <h2>Распознанные скриншоты</h2>
        <span>${shots.length} шт.</span>
      </div>
      ${shots
        .map(
          (sh) => `
        <div class="card shot-card">
          ${sh.filename ? `<img class="shot-thumb" src="/media/screenshots/${sh.filename}" alt="" />` : ""}
          <div style="flex:1;min-width:0;">
            <div class="director-tag">${sh.extracted_director || "Из входящих"}</div>
            <div style="font-size:16px;font-weight:700;margin:2px 0;">${sh.extracted_title}</div>
            <div class="venue-line">${sh.extracted_venue} ${sh.extracted_dates ? "· " + sh.extracted_dates : ""}</div>
          </div>
        </div>
      `
        )
        .join("")}
    `
        : ""
    }
  `;
}

/* ================= ВКЛАДКА 3: НРАВЯТСЯ (КОГО ОТСЛЕЖИВАТЬ + ЛАЙКНУТЫЕ СПЕКТАКЛИ) ================= */
let likedAddMode = "creator"; // 'creator' | 'play'

window.setLikedAddMode = (mode) => {
  likedAddMode = mode === "play" ? "play" : "creator";
  renderAll();
};

window.submitInlineCreator = async () => {
  const inp = document.getElementById("inlineCreatorName");
  const roleEl = document.getElementById("inlineCreatorRole");
  const name = inp ? inp.value.trim() : "";
  const role = roleEl ? roleEl.value : "Режиссёр";
  if (!name) return showToast("Впишите режиссёра, хореографа или театр");
  await fetch("/api/tracked-creators", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      person_name: state.activePerson,
      name,
      role,
      source_note: "Добавлено вручную",
    }),
  });
  await loadState();
  showToast(`Отслеживаем: ${name}`);
};

window.deleteTrackedCreator = async (creatorId) => {
  await fetch(`/api/tracked-creators/${creatorId}`, { method: "DELETE" });
  await loadState();
  showToast("Убрано из отслеживания");
};

function renderLikedTab() {
  const likedData = state.data.liked || { tracked_creators: [], liked_plays: [] };
  const creators = likedData.tracked_creators || [];
  const likedPlays = likedData.liked_plays || [];
  const pendingReviews = (state.data.post_show && state.data.post_show.pending) || [];
  const allCatalog = state.data.repertoire || [];

  return `
    ${
      pendingReviews.length
        ? pendingReviews
            .map((rv) => {
              const payload = JSON.stringify(rv).replace(/'/g, "&#39;");
              return `
        <div class="card" style="border-color:rgba(212,175,106,0.6);margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
            <div>
              <div class="director-tag">После спектакля${rv.label ? " · " + rv.label : ""}</div>
              <h3 class="card-title" style="margin-top:3px;font-size:17px;">Как вам «${rv.title}»?</h3>
              ${rv.venue_name ? `<div class="venue-line">${rv.venue_name}</div>` : ""}
            </div>
            <button type="button" class="btn-ghost" style="padding:4px 9px;font-size:11.5px;" title="Скрыть" onclick='submitPostShowReview(${payload}, "dismissed")'>✕</button>
          </div>
          <div style="display:flex;gap:10px;margin-top:11px;">
            <button type="button" class="verdict-btn like" style="flex:1;" onclick='submitPostShowReview(${payload}, "liked")'>${iconWhiteHeart(34)} <span>Понравилось</span></button>
            <button type="button" class="verdict-btn dislike" style="flex:1;" onclick='submitPostShowReview(${payload}, "disliked")'>${iconWhiteSkull(34)} <span>Не понравилось</span></button>
          </div>
        </div>
      `;
            })
            .join("")
        : ""
    }

    <div class="card" style="margin-bottom:14px;border-color:rgba(212,175,106,0.38);">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:9px;">
        <span style="font-size:13px;font-weight:700;">Добавить вручную</span>
        <div style="display:inline-flex;background:var(--surface-2);border:1px solid var(--border);border-radius:999px;padding:2px;gap:2px;">
          <button type="button" style="border:none;border-radius:999px;padding:4px 10px;font-size:11.5px;font-weight:700;cursor:pointer;background:${likedAddMode === "creator" ? "var(--gold)" : "transparent"};color:${likedAddMode === "creator" ? "#14110B" : "var(--muted)"};" onclick="setLikedAddMode('creator')">Кого отслеживать</button>
          <button type="button" style="border:none;border-radius:999px;padding:4px 10px;font-size:11.5px;font-weight:700;cursor:pointer;background:${likedAddMode === "play" ? "var(--gold)" : "transparent"};color:${likedAddMode === "play" ? "#14110B" : "var(--muted)"};" onclick="setLikedAddMode('play')">Спектакль</button>
        </div>
      </div>

      ${
        likedAddMode === "creator"
          ? `
        <div style="display:flex;gap:6px;align-items:center;">
          <input id="inlineCreatorName" class="input-field" style="flex:1;padding:9px 12px;font-size:13.5px;" placeholder="Режиссёр, хореограф или театр..." onkeydown="if(event.key==='Enter'){submitInlineCreator();}" />
          <select id="inlineCreatorRole" class="input-field" style="width:112px;padding:9px 8px;font-size:12.5px;">
            <option value="Режиссёр">Режиссёр</option>
            <option value="Хореограф">Хореограф</option>
            <option value="Театр">Театр</option>
          </select>
          <button type="button" class="btn-primary" style="padding:9px 14px;flex-shrink:0;" onclick="submitInlineCreator()">+</button>
        </div>
      `
          : `
        <div style="display:flex;flex-direction:column;gap:7px;">
          <select class="input-field" style="padding:9px 11px;font-size:13px;" onchange="prefillInlinePlayFromCatalog(this.value)">
            <option value="">— Выбрать из каталога (или впишите ниже) —</option>
            ${allCatalog
              .map(
                (p) =>
                  `<option value="${p.slug}">${p.title} (${p.venue_name} · ${p.stage_director || p.director})</option>`
              )
              .join("")}
          </select>
          <input id="inlinePlaySlug" type="hidden" value="" />
          <div style="display:flex;gap:6px;">
            <input id="inlinePlayTitle" class="input-field" style="flex:1;padding:9px 12px;font-size:13.5px;" placeholder="Название спектакля..." />
            <input id="inlinePlayDirector" class="input-field" style="flex:1;padding:9px 12px;font-size:13.5px;" placeholder="Режиссёр (необяз.)" />
            <button type="button" class="btn-primary" style="padding:9px 14px;flex-shrink:0;" onclick="submitInlinePlay()">+</button>
          </div>
        </div>
      `
      }
    </div>

    <div class="section-head" style="margin-top:2px;">
      <h2>Кто нравится · Отслеживаем</h2>
      <span>${creators.length}</span>
    </div>
    ${
      creators.length
        ? `<div style="display:flex;flex-direction:column;gap:7px;margin-bottom:14px;">
            ${creators
              .map(
                (cr) => `
              <div class="card" style="padding:10px 12px;margin-bottom:0;display:flex;justify-content:space-between;align-items:center;gap:10px;">
                <div style="min-width:0;">
                  <div style="font-size:15px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:6px;">${iconWhiteHeart(16)} <span>${cr.name}</span></div>
                  <div class="venue-line" style="font-size:12px;">${cr.role}${cr.source_note ? " · " + cr.source_note : ""}</div>
                </div>
                <button type="button" class="btn-ghost" style="padding:5px 10px;font-size:12px;" title="Убрать" onclick="deleteTrackedCreator(${cr.id})">✕</button>
              </div>
            `
              )
              .join("")}
          </div>`
        : `<div class="card"><div class="venue-line">Впишите выше режиссёра, хореографа или театр, чтобы отслеживать новые постановки.</div></div>`
    }

    <div class="section-head">
      <h2>Спектакли (${likedPlays.length})</h2>
    </div>

    ${
      likedPlays.length
        ? likedPlays
            .map((lp) => {
              const evs = lp.upcoming_events || [];
              const badges = [];
              if (lp.both_seen || (lp.seen_by && lp.seen_by.length)) {
                badges.push(`<span class="badge">✓ Видели</span>`);
              }
              if (lp.explicitly_liked) {
                badges.push(`<span class="badge verified" style="display:inline-flex;align-items:center;gap:4px;">${iconWhiteHeart(14)} Лайк</span>`);
              } else if (lp.swiped_right) {
                badges.push(`<span class="badge legroom">Хочу →</span>`);
              }
              return `
        <div class="card" style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
          <div style="min-width:0;flex:1;">
            <div class="director-tag">${lp.venue_name}</div>
            <h3 class="card-title" style="font-size:17px;">${lp.title}</h3>
            ${
              lp.stage_director
                ? `<div class="creator-line"><span class="creator-role-lbl">${lp.director_role}:</span> ${lp.stage_director}</div>`
                : ""
            }
            <div style="margin-top:7px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
              ${badges.join("")}
              ${
                lp.swiped_right && evs.length
                  ? `<button type="button" class="pill-btn" onclick="openDateEventSheet('${lp.slug}', ${evs[0].id})">${evs[0].weekday}, ${evs[0].label} · ${evs[0].event_time} ›</button>`
                  : ""
              }
            </div>
          </div>
          ${
            lp.kind === "manual"
              ? `<button type="button" class="btn-ghost" style="padding:6px 10px;font-size:12px;" title="Убрать" onclick="deleteManualLiked(${lp.id})">✕</button>`
              : `<button type="button" class="btn-ghost" style="padding:6px 10px;font-size:12px;" title="Убрать из «Нравятся»" onclick="toggleCatalogLike('${lp.slug}', false)">✕</button>`
          }
        </div>
      `;
            })
            .join("")
        : `<div class="card"><div class="venue-line">Сюда попадают спектакли после лайка (или свайпа вправо «Хочу →»).</div></div>`
    }
  `;
}

window.prefillInlinePlayFromCatalog = (slug) => {
  const prod = (state.data.repertoire || []).find((p) => p.slug === slug);
  const slugEl = document.getElementById("inlinePlaySlug");
  const tEl = document.getElementById("inlinePlayTitle");
  const dEl = document.getElementById("inlinePlayDirector");
  if (!prod) {
    if (slugEl) slugEl.value = "";
    return;
  }
  if (slugEl) slugEl.value = prod.slug;
  if (tEl) tEl.value = prod.title;
  if (dEl) dEl.value = prod.stage_director || prod.director || "";
};

window.submitInlinePlay = async () => {
  const slug = (document.getElementById("inlinePlaySlug") || {}).value || "";
  const title = ((document.getElementById("inlinePlayTitle") || {}).value || "").trim();
  const stage_director = ((document.getElementById("inlinePlayDirector") || {}).value || "").trim();

  if (slug) {
    await fetch("/api/liked/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, liked: true, person_name: state.activePerson }),
    });
  } else {
    if (!title) return showToast("Укажите название спектакля");
    await fetch("/api/liked/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        stage_director,
        person_name: state.activePerson,
      }),
    });
  }
  await loadState();
  showToast("♥ Добавлено в «Нравятся»");
};

const promptedPostShowKeys = new Set();

function maybeOpenPostShowPrompt() {
  const pending = (state.data && state.data.post_show && state.data.post_show.pending) || [];
  if (!pending.length) return;
  const backdrop = document.getElementById("sheetBackdrop");
  if (backdrop && !backdrop.classList.contains("hidden")) return;

  const nextRv = pending.find((rv) => !promptedPostShowKeys.has(`${state.activePerson}:${rv.item_key}`));
  if (!nextRv) return;
  promptedPostShowKeys.add(`${state.activePerson}:${nextRv.item_key}`);
  openPostShowSheet(nextRv);
}

window.openPostShowSheet = (rv) => {
  const payload = JSON.stringify(rv).replace(/'/g, "&#39;");
  openSheet(`
    <div class="director-tag">После спектакля${rv.full_date ? " · " + rv.full_date : ""}</div>
    <h3 style="margin:4px 0 6px;font-size:20px;">Понравился спектакль «${rv.title}»?</h3>
    ${rv.venue_name ? `<div class="venue-line" style="margin-bottom:4px;">${rv.venue_name}</div>` : ""}
    ${rv.stage_director ? `<div class="creator-line" style="margin-bottom:10px;"><span class="creator-role-lbl">Постановка:</span> ${rv.stage_director}</div>` : ""}
    <div class="venue-line" style="margin-bottom:14px;">
      Если нажать «Понравилось», спектакль добавится во вкладку «Нравятся», а агент учтёт его автора для будущих подборок.
    </div>
    <div style="display:flex;gap:10px;">
      <button type="button" class="verdict-btn like" style="flex:1;" onclick='submitPostShowReview(${payload}, "liked")'>
        ${iconWhiteHeart(38)} <span>Понравилось</span>
      </button>
      <button type="button" class="verdict-btn dislike" style="flex:1;" onclick='submitPostShowReview(${payload}, "disliked")'>
        ${iconWhiteSkull(38)} <span>Не понравилось</span>
      </button>
    </div>
  `);
};

window.submitPostShowReview = async (rv, verdict) => {
  await fetch("/api/post-show-review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_key: rv.item_key,
      person_name: state.activePerson,
      verdict,
      title: rv.title,
      venue_name: rv.venue_name || "",
      stage_director: rv.stage_director || "",
      production_slug: rv.production_slug || "",
      event_date: rv.event_date || "",
    }),
  });
  closeSheet();
  await loadState();
  if (verdict === "liked") {
    showToast("♥ Добавлено в «Нравятся»!");
  } else if (verdict === "disliked") {
    showToast("Запомнили оценку");
  }
};

window.openAddLikedPlaySheet = () => {
  const all = state.data.repertoire || [];
  openSheet(`
    <h3 style="margin:0 0 8px;">Добавить в «Нравятся»</h3>
    <div class="venue-line" style="margin-bottom:12px;">
      Впишите спектакль, который вам понравился — он появится во вкладке «Нравятся».
    </div>

    <div class="form-group">
      <div class="form-label">Выбрать из каталога (необязательно)</div>
      <select class="input-field" onchange="prefillLikedFromCatalog(this.value)">
        <option value="">— Или впишите вручную ниже —</option>
        ${all
          .map(
            (p) => `
          <option value="${p.slug}">${p.title} (${p.venue_name} · ${p.stage_director || p.director})</option>
        `
          )
          .join("")}
      </select>
    </div>

    <input id="lkSlug" type="hidden" value="" />
    <div class="form-group">
      <div class="form-label">Название спектакля</div>
      <input id="lkTitle" class="input-field" placeholder="Например: Гроза / Бег / Дон Кихот" />
    </div>
    <div class="form-group">
      <div class="form-label">Режиссёр или хореограф (необязательно)</div>
      <input id="lkDirector" class="input-field" placeholder="Например: Антон Фёдоров" />
    </div>
    <div class="form-group">
      <div class="form-label">Театр (необязательно)</div>
      <input id="lkVenue" class="input-field" placeholder="Например: Театр «Около»" />
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;" onclick="saveLikedPlayForm()">ОК — добавить</button>
  `);
};

window.prefillLikedFromCatalog = (slug) => {
  const prod = (state.data.repertoire || []).find((p) => p.slug === slug);
  if (!prod) return;
  document.getElementById("lkSlug").value = prod.slug;
  document.getElementById("lkTitle").value = prod.title;
  document.getElementById("lkDirector").value = prod.stage_director || prod.director;
  document.getElementById("lkVenue").value = prod.venue_name;
};

window.saveLikedPlayForm = async () => {
  const slug = document.getElementById("lkSlug").value;
  const title = document.getElementById("lkTitle").value.trim();
  const stage_director = document.getElementById("lkDirector").value.trim();
  const venue_name = document.getElementById("lkVenue").value.trim();

  if (slug) {
    await fetch("/api/liked/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, liked: true, person_name: state.activePerson }),
    });
  } else {
    if (!title) return showToast("Укажите название спектакля");
    await fetch("/api/liked/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, stage_director, venue_name }),
    });
  }
  closeSheet();
  await loadState();
  showToast("Добавлено в «Нравятся»");
};

window.toggleCatalogLike = async (slug, liked) => {
  await fetch("/api/liked/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug, liked, person_name: state.activePerson }),
  });
  await loadState();
  showToast(liked ? "Добавлено в «Нравятся»" : "Убрано из «Нравятся»");
};

window.deleteManualLiked = async (id) => {
  await fetch(`/api/liked/manual/${id}`, { method: "DELETE" });
  await loadState();
  showToast("Убрано из «Нравятся»");
};

function renderProfileTab() {
  const comps = state.data.companions || [];
  const awayPeriods = state.data.away_periods || [];
  const s = state.data.settings;
  const sources = state.data.sources || [];
  const tp = (state.data.taste_profiles && state.data.taste_profiles[state.activePerson]) || {
    favorites: [],
    dislikes: [],
    rules: [],
    swiped_total: 0,
  };
  const cardConnected = Boolean(s.card_masked);
  const payModeLabel =
    s.payment_method === "sbp"
      ? "СБП (пуш / выбор банка на iPhone)"
      : s.card_masked
      ? `${s.card_masked} · автооплата до 3D-Secure`
      : "Карта РФ не привязана (автооплата до 3D-Secure)";
  const mosConnected = Boolean(s.mos_id_connected);

  return `
    <div class="section-head" style="margin-top:2px;">
      <h2>1. Зрители А и +1 (паспорта и ФИО)</h2>
    </div>
    <div class="venue-line" style="margin-bottom:9px;">
      Заполняются только здесь на iPhone (шифруются локально на устройстве, в чат и логи никогда не попадают). Нужны для именных билетов Москвы.
    </div>
    ${comps
      .filter((c) => c.name === "А" || c.name === "+1")
      .map((c) => {
        const fioClean = c.full_name && c.full_name !== "Основной пользователь" ? c.full_name : "";
        const docOk = Boolean(c.doc_masked && !c.doc_masked.startsWith("Не заполнен") && c.doc_masked !== "Без документа");
        const ready = Boolean(fioClean && docOk);
        return `
      <div class="card" style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
        <div style="min-width:0;">
          <div style="display:flex;align-items:center;gap:7px;">
            <span style="font-weight:800;font-size:16px;">${c.name}</span>
            <span class="badge ${ready ? "verified" : ""}" style="font-size:10.5px;">${ready ? "✓ Готов к покупке" : "Не заполнен"}</span>
          </div>
          <div class="venue-line" style="margin-top:3px;">${fioClean || "Нажмите «Заполнить», чтобы указать ФИО и паспорт"}</div>
          <div class="venue-line" style="font-size:11.5px;">${docOk ? c.doc_masked : "Паспорт не указан"}${c.birth_date ? " · д.р. " + c.birth_date : ""}${c.email ? " · " + c.email : ""}</div>
        </div>
        <button type="button" class="${ready ? "btn-ghost" : "btn-primary"}" style="flex-shrink:0;" onclick='openCompanionSheet(${JSON.stringify(c)})'>${ready ? "Изм." : "Заполнить"}</button>
      </div>
    `;
      })
      .join("")}

    <div class="section-head">
      <h2>2. Карта РФ для автооплаты (до 3D-Secure)</h2>
    </div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
        <div style="min-width:0;">
          <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;">
            <span style="font-weight:700;font-size:15px;">${payModeLabel}</span>
            <span class="badge ${cardConnected ? "verified" : ""}" style="font-size:10.5px;">${cardConnected ? "✓ Привязана" : "Нужна карта"}</span>
          </div>
          <div class="venue-line" style="margin-top:3px;">Агент сам вводит карту в кассе и доводит до пуша / СМС 3D-Secure · до ${fmtRub(s.max_price_per_ticket)}/бил.</div>
        </div>
        <button type="button" class="${cardConnected ? "btn-ghost" : "btn-primary"}" style="flex-shrink:0;" onclick="openCardSheet()">${cardConnected ? "Изм." : "Карта"}</button>
      </div>
    </div>

    <div class="section-head">
      <h2>3. Каналы касс и вход Mos ID</h2>
    </div>
    <div class="card">
      <div class="venue-line" style="color:var(--text);margin-bottom:8px;">
        <b>Каскад поиска лучших мест:</b><br/>
        1) Прямой сайт театра (0% сбора) → 2) Яндекс Афиша и Ticketland → 3) Mos ID для «Около» и театров Депкульта.
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding-top:8px;border-top:1px solid var(--border);">
        <div style="min-width:0;">
          <div style="display:flex;align-items:center;gap:7px;">
            <span style="font-weight:700;font-size:14.5px;">${mosConnected ? s.mos_id_masked : "Mos ID (mos.ru)"}</span>
            <span class="badge ${mosConnected ? "verified" : ""}" style="font-size:10.5px;">${mosConnected ? "✓ Подключён" : "Не подключён"}</span>
          </div>
          <div class="venue-line" style="font-size:11.5px;">Для квот на bilet.mos.ru («Около» и др.)</div>
        </div>
        <button type="button" class="${mosConnected ? "btn-ghost" : "btn-primary"}" style="flex-shrink:0;" onclick="openMosIdSheet()">${mosConnected ? "Изм." : "Mos ID"}</button>
      </div>
      <button type="button" class="btn-primary" style="width:100%;margin-top:11px;padding:11px;" onclick="startTestCheckoutFromProfile()">
        Протестировать покупку билетов →
      </button>
    </div>

    <div class="section-head">
      <h2>Вкус и предпочтения: ${state.activePerson}</h2>
      <button type="button" class="pill-btn" onclick="openPersonRuleSheet()">+ Исключение</button>
    </div>
    <div class="card">
      <div class="venue-line" style="margin-bottom:8px;">
        Лента и расписание подстраиваются отдельно под <b>${state.activePerson}</b> по каждому свайпу («Хочу», «Не хочу», «Уже был»).
      </div>
      ${
        tp.rules && tp.rules.length
          ? `<div style="margin-bottom:8px;">
              <div class="form-label" style="margin-bottom:4px;">Жёсткие исключения для ${state.activePerson}:</div>
              ${tp.rules
                .map(
                  (r) => `
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 10px;margin-top:4px;border-radius:10px;background:var(--surface-2);border:1px solid var(--border);">
                  <span style="font-size:12.5px;font-weight:600;">✕ ${r.label}</span>
                  <button type="button" class="btn-ghost" style="padding:3px 8px;font-size:11px;" onclick="deletePersonRule(${r.id})">Убрать</button>
                </div>
              `
                )
                .join("")}
            </div>`
          : `<div class="venue-line" style="margin-bottom:8px;">Жёстких исключений для ${state.activePerson} нет.</div>`
      }
      ${
        tp.favorites.length || tp.dislikes.length
          ? `<div>
              <div class="form-label" style="margin-bottom:4px;">Выучено по свайпам (${tp.swiped_total}):</div>
              ${tp.favorites.map((f) => `<span class="taste-pill pos">↑ ${f.label}</span>`).join("")}
              ${tp.dislikes.map((d) => `<span class="taste-pill neg">↓ ${d.label}</span>`).join("")}
            </div>`
          : `<div class="venue-line" style="font-size:11.5px;">Сделайте несколько свайпов во вкладке «Отбор» — здесь появятся выученные предпочтения для ${state.activePerson}.</div>`
      }
    </div>

    <div class="section-head">
      <h2>Театры, выставки и инфлюенсеры</h2>
      <button type="button" class="pill-btn" onclick="openSourceSheet()">+ Источник</button>
    </div>
    ${sources
      .map(
        (src) => `
      <div class="card" style="padding:11px 14px;display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <div style="min-width:0;">
          <div class="director-tag">${src.kind}</div>
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${src.title}</div>
          <div class="venue-line" style="font-size:11.5px;">${src.handle_or_url}</div>
        </div>
        <button type="button" class="btn-ghost" style="padding:6px 10px;" onclick="deleteSource(${src.id})">✕</button>
      </div>
    `
      )
      .join("")}

    <div class="card" style="margin-top:12px;display:flex;justify-content:space-between;align-items:center;gap:10px;">
      <div>
        <div style="font-size:13px;font-weight:700;">Автопроверка касс раз в сутки (${s.daily_scan_time})</div>
        <div class="venue-line" style="font-size:11.5px;">${s.last_scan_at ? `Последняя сверка: ${s.last_scan_at}` : s.last_scan_summary}</div>
      </div>
      <button type="button" class="btn-ghost" onclick="runManualScan(this)">Проверить сейчас</button>
    </div>
  `;
}

function renderAll() {
  if (!state.data) return;
  renderTopAndBanner();
  const main = document.getElementById("mainView");
  if (state.tab === "swipe") {
    main.innerHTML = renderSwipeTab();
    bindSwipePhysics();
  } else if (state.tab === "week") {
    main.innerHTML = renderWeekTab();
  } else if (state.tab === "liked") {
    main.innerHTML = renderLikedTab();
  } else if (state.tab === "tickets") {
    main.innerHTML = renderTicketsTab();
    document.querySelectorAll(".pending-order-card[data-order-id]").forEach((card) => {
      bindDismissSwipeLeft(card, () => cancelOrder(Number(card.dataset.orderId)));
    });
  } else if (state.tab === "profile") {
    main.innerHTML = renderProfileTab();
  }
}

document.querySelectorAll(".tab-item").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

window.runManualScan = async (btn) => {
  if (btn) btn.textContent = "Сверяем…";
  try {
    const res = await fetch("/api/scan", { method: "POST" });
    const out = await res.json();
    await loadState();
    showToast(`Кассы сверены: ${out.scan.live_hits} спектаклей`);
  } finally {
    if (btn) btn.textContent = "Проверить сейчас";
  }
};

window.openPersonRuleSheet = () => {
  openSheet(`
    <h3 style="margin:0 0 8px;">Не показывать для ${state.activePerson}</h3>
    <div class="venue-line" style="margin-bottom:12px;">
      Выберите жанр или направление, которое не нужно предлагать в профиле <b>${state.activePerson}</b> (у второго человека оно останется).
    </div>
    <div class="form-group">
      <div class="form-label">Что скрыть для ${state.activePerson}</div>
      <select id="prPreset" class="input-field">
        <option value="exclude_bolshoi_opera|bolshoi_opera|Не показывать оперу в Большом театре">Опера в Большом театре</option>
        <option value="exclude_bolshoi_ballet|bolshoi_ballet|Не показывать балет в Большом театре">Балет в Большом театре</option>
        <option value="exclude_category|special|Не показывать кинопоказы и лекции">Кинопоказы / видеоверсии</option>
        <option value="exclude_director|Римас Туминас|Не показывать постановки Римаса Туминаса">Римас Туминас</option>
        <option value="exclude_director|Юрий Бутусов|Не показывать постановки Юрия Бутусова">Юрий Бутусов</option>
        <option value="exclude_director|Дмитрий Крымов|Не показывать постановки Дмитрия Крымова">Дмитрий Крымов</option>
        <option value="exclude_director|Театр «Озеро»|Не показывать Театр «Озеро»">Театр «Озеро»</option>
      </select>
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;" onclick="savePersonRule()">Сохранить для ${state.activePerson}</button>
  `);
};

window.savePersonRule = async () => {
  const raw = document.getElementById("prPreset").value;
  const [rule_kind, rule_value, label] = raw.split("|");
  await fetch("/api/person-rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      person_name: state.activePerson,
      rule_kind,
      rule_value,
      label,
    }),
  });
  closeSheet();
  await loadState();
  showToast(`Правило сохранено для ${state.activePerson}`);
};

window.deletePersonRule = async (ruleId) => {
  await fetch(`/api/person-rules/${ruleId}`, { method: "DELETE" });
  await loadState();
  showToast("Исключение убрано");
};

window.changeWeek = (delta) => {
  const maxW = ((state.data.proposals && state.data.proposals.weeks) || []).length - 1;
  state.weekIndex = Math.max(0, Math.min(maxW, state.weekIndex + delta));
  window.scrollTo(0, 0);
  renderAll();
};

window.scrollToDay = (isoDate) => {
  const el = document.getElementById(`day-${isoDate}`);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
};

window.toggleBusyDayOffers = (isoDate) => {
  state.expandedBusyDates[isoDate] = !state.expandedBusyDates[isoDate];
  renderAll();
};

window.updateProdStatus = async (slug, status) => {
  await fetch(`/api/productions/${slug}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, person_name: state.activePerson }),
  });
  await loadState();
  showToast("Обновлено");
};

window.showHallSheet = (slug) => {
  const prod = (state.data.repertoire || []).find((p) => p.slug === slug);
  if (!prod) return;
  openSheet(`
    <div class="director-tag">${prod.venue_name}</div>
    <h3 style="margin:4px 0 8px;font-size:19px;">Приоритет мест: «${prod.title}»</h3>
    ${(prod.seat_targets || [])
      .map(
        (t) => `
      <div class="card" style="background:var(--surface-2);margin-bottom:9px;">
        <div style="display:flex;justify-content:space-between;">
          <b>#${t.rank} · ${t.zone}, ряд ${t.row} (${t.seats})</b>
          ${t.legroom ? `<span class="badge legroom">Просторно ногам</span>` : ""}
        </div>
        <div class="venue-line" style="margin-top:5px;">${t.why}</div>
      </div>
    `
      )
      .join("")}
    <div style="margin-top:12px;display:flex;gap:8px;">
      <a href="${prod.official_url}" target="_blank" rel="noopener" class="btn-ghost" style="text-align:center;text-decoration:none;flex:1;">Офиц. касса ↗</a>
      <button type="button" class="btn-primary" style="flex:1;" onclick="closeSheet()">Закрыть</button>
    </div>
  `);
};

window.startCheckout = async (eventId) => {
  const sel = state.eventSelection[eventId] || { count: 2, ids: [1, 2] };
  const res = await fetch("/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_id: eventId, ticket_count: sel.count, party_ids: sel.ids }),
  });
  const out = await res.json();
  await loadState();
  if (out.order) openOrderConfirmSheet(out.order);
};

window.openOrderConfirmSheet = (order) => {
  openSheet(`
    <div class="director-tag">Ожидание пуша в банке · бронь до ${order.hold_expires_at}</div>
    <h3 style="margin:4px 0 6px;font-size:20px;">${order.production_title}</h3>
    <div class="venue-line">${order.venue_name} · ${order.event_date} в ${order.event_time}</div>
    <div class="card" style="background:var(--surface-2);margin-top:12px;">
      <div style="font-weight:700;">${order.seat_selection}</div>
      <div class="venue-line" style="margin-top:4px;">Кто идёт: <b>${order.party_names}</b></div>
      <div style="font-size:18px;font-weight:800;color:var(--gold);margin-top:8px;">Итого: ${fmtRub(order.total_price)}</div>
    </div>
    <div class="form-group">
      <input id="threeDsInput" class="input-field" type="text" inputmode="numeric" placeholder="Код 3D-Secure (если банк прислал СМС вместо пуша)" />
    </div>
    <div style="display:flex;gap:8px;margin-top:12px;">
      <a href="${order.official_url}" target="_blank" rel="noopener" class="btn-ghost" style="text-decoration:none;text-align:center;">Касса ↗</a>
      <button type="button" class="btn-primary" style="flex:1;" onclick="confirmOrderNow(${order.id})">Подтверждено в банке ✓</button>
    </div>
  `);
};

window.confirmOrderNow = async (orderId) => {
  const codeEl = document.getElementById("threeDsInput");
  await fetch(`/api/orders/${orderId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ three_ds_code: codeEl ? codeEl.value : "" }),
  });
  closeSheet();
  await loadState();
  showToast("🎟 Билет встал как «Уже есть» в расписание!");
};

window.cancelOrder = async (orderId) => {
  await fetch(`/api/orders/${orderId}/cancel`, { method: "POST" });
  await loadState();
  showToast("Бронь отменена");
};

window.handleIncomingUpload = async (input, forceKind = "auto") => {
  if (!input.files || !input.files.length) return;
  showToast("Читаем файл…");
  let lastKind = "wishlist";
  for (const file of input.files) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("force_kind", forceKind);
    const res = await fetch("/api/incoming/upload", { method: "POST", body: fd });
    const out = await res.json();
    if (out.item) lastKind = out.item.kind;
  }
  input.value = "";
  await loadState();
  showToast(lastKind === "ticket" ? "🎟 Билет сохранён в «Уже есть»!" : "⚡ Скриншот распознан!");
};

window.openManualTicketSheet = () => {
  openSheet(`
    <h3 style="margin:0 0 10px;">Добавить купленный билет</h3>
    <div class="form-group">
      <div class="form-label">Спектакль или выставка</div>
      <input id="mtTitle" class="input-field" placeholder="Например: Евгений Онегин" />
    </div>
    <div class="form-group">
      <div class="form-label">Театр / площадка</div>
      <input id="mtVenue" class="input-field" placeholder="Театр им. Вахтангова" />
    </div>
    <div style="display:grid;grid-template-columns:1.3fr 1fr;gap:8px;">
      <div class="form-group">
        <div class="form-label">Дата</div>
        <input id="mtDate" type="date" class="input-field" />
      </div>
      <div class="form-group">
        <div class="form-label">Время</div>
        <input id="mtTime" type="time" class="input-field" value="19:00" />
      </div>
    </div>
    <div class="form-group">
      <div class="form-label">Ряд и места</div>
      <input id="mtSeats" class="input-field" placeholder="Партер, ряд 5, места 12–13" />
    </div>
    <div class="form-group" style="display:flex;align-items:center;justify-content:space-between;margin-top:4px;">
      <div class="form-label">Кто идёт</div>
      <div class="person-pair-pill">
        <button type="button" class="person-pair-btn dm-who-chip active" data-who="А" onclick="toggleManualWhoChip(this)">А</button>
        <button type="button" class="person-pair-btn dm-who-chip active" data-who="+1" onclick="toggleManualWhoChip(this)">+1</button>
      </div>
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;" onclick="saveManualTicket()">Сохранить</button>
  `);
};

window.saveManualTicket = async () => {
  const title = document.getElementById("mtTitle").value.trim();
  const event_date = document.getElementById("mtDate").value;
  if (!title || !event_date) return showToast("Укажите название и дату");
  await fetch("/api/wallet-tickets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      venue_name: document.getElementById("mtVenue").value,
      event_date,
      event_time: document.getElementById("mtTime").value || "19:00",
      seats_info: document.getElementById("mtSeats").value,
      who_goes: readManualWhoSelection(),
    }),
  });
  closeSheet();
  await loadState();
  showToast("🎟 Билет добавлен в расписание!");
};

window.deleteWalletTicket = async (id) => {
  await fetch(`/api/wallet-tickets/${id}`, { method: "DELETE" });
  await loadState();
  showToast("Билет удалён");
};

window.openMailSheet = () => {
  const s = (state.data && state.data.settings) || {};
  const addr = s.mail_address || "Lesteatres@mail.ru";
  const host = s.mail_imap_host || "imap.mail.ru";
  openSheet(`
    <h3 style="margin:0 0 8px;">Почта для билетов</h3>
    <div class="venue-line" style="line-height:1.45;margin-bottom:10px;">
      Агент каждые 3 минуты проверяет <b>${addr}</b> по защищённому IMAP, читает письма касс и PDF-билеты и ставит их в расписание.<br/><br/>
      В Mail.ru создайте пароль для внешнего приложения:<br/>
      <b>Аккаунт Mail.ru → Безопасность → Пароли для внешних приложений</b> — и вставьте 16-значный пароль ниже (хранится в зашифрованном виде).
    </div>
    <div class="form-group">
      <div class="form-label">Адрес почты билетов</div>
      <input id="mailAddr" class="input-field" value="${addr}" />
    </div>
    <div class="form-group">
      <div class="form-label">Пароль внешнего приложения Mail.ru</div>
      <input id="mailPwd" type="password" class="input-field" placeholder="xxxx xxxx xxxx xxxx" autocomplete="off" />
    </div>
    <input id="mailHost" type="hidden" value="${host}" />
    <button type="button" id="mailSaveBtn" class="btn-primary" style="width:100%;margin-top:6px;" onclick="saveMailInbox()">Подключить и проверить билеты</button>
  `);
};

window.saveMailInbox = async () => {
  const btn = document.getElementById("mailSaveBtn");
  const password = document.getElementById("mailPwd").value.trim();
  const address = document.getElementById("mailAddr").value.trim() || "Lesteatres@mail.ru";
  const imap_host = document.getElementById("mailHost").value.trim() || "imap.mail.ru";
  if (!password) return showToast("Введите пароль приложения Mail.ru");
  if (btn) btn.textContent = "Проверяем вход в Mail.ru…";
  try {
    const res = await fetch("/api/settings/mail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password, address, imap_host }),
    });
    const out = await res.json();
    if (!res.ok) return showToast(out.detail || "Ошибка подключения к Mail.ru");
    closeSheet();
    await loadState();
    showToast(
      out.imported_now > 0
        ? `Почта подключена! Найдено билетов: ${out.imported_now}`
        : `Почта ${out.address} подключена!`
    );
  } finally {
    if (btn) btn.textContent = "Подключить и проверить билеты";
  }
};

window.checkMailNow = async (btn) => {
  if (btn) btn.textContent = "Читаем…";
  try {
    const res = await fetch("/api/mail/check", { method: "POST" });
    const out = await res.json();
    await loadState();
    if (!out.connected) {
      showToast(out.error || "Проверьте пароль почты");
    } else if (out.imported > 0) {
      showToast(`🎟 Добавлено новых билетов с почты: ${out.imported}`);
    } else {
      showToast("Почта проверена — новых билетов пока нет");
    }
  } finally {
    if (btn) btn.textContent = "Проверить";
  }
};

window.openTelegramSheet = () => {
  const s = (state.data && state.data.settings) || {};
  openSheet(`
    <h3 style="margin:0 0 8px;">Telegram-бот</h3>
    <div class="venue-line" style="line-height:1.45;margin-bottom:10px;">
      Текущий статус: <b style="color:var(--gold);">${s.tg_bot_username || "не подключён"}</b><br/>
      Все PDF билетов, скриншоты афиш и посты, которые вы перешлёте этому боту в Telegram, сразу попадут в приложение и расписание.
    </div>
    <div class="form-group">
      <div class="form-label">Токен от @BotFather</div>
      <input id="tgTok" class="input-field" placeholder="123456789:AA..." />
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;" onclick="saveTelegramBot()">Сохранить токен</button>
  `);
};

window.saveTelegramBot = async () => {
  const res = await fetch("/api/settings/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bot_token: document.getElementById("tgTok").value }),
  });
  const out = await res.json();
  if (!res.ok) return showToast(out.detail || "Ошибка токена");
  closeSheet();
  await loadState();
  showToast(`Бот ${out.username} подключён!`);
};

window.openCompanionSheet = (comp = null) => {
  if (!comp) return;
  const s = (state.data && state.data.settings) || {};
  const defaultMail = comp.email || s.mail_address || "Lesteatres@mail.ru";
  const curDocType = comp.doc_type || "Паспорт РФ";
  const fioVal = comp.full_name && comp.full_name !== "Основной пользователь" ? comp.full_name : "";
  const docPlaceholder =
    comp.doc_masked && !comp.doc_masked.startsWith("Не заполнен")
      ? comp.doc_masked + " (оставьте пустым, чтобы не менять)"
      : "4510 123456";
  openSheet(`
    <div class="director-tag">Локальное шифрование на устройстве</div>
    <h3 style="margin:3px 0 10px;">Зритель: ${comp.name}</h3>
    <input id="cName" type="hidden" value="${comp.name}" />
    <div class="form-group">
      <div class="form-label">ФИО полностью (как в паспорте, для именного билета)</div>
      <input id="cFull" class="input-field" value="${fioVal}" placeholder="Фамилия Имя Отчество" />
    </div>
    <div style="display:grid;grid-template-columns:1fr 1.15fr;gap:8px;">
      <div class="form-group">
        <div class="form-label">Дата рождения</div>
        <input id="cBirth" type="date" class="input-field" value="${comp.birth_date || ""}" />
      </div>
      <div class="form-group">
        <div class="form-label">Документ</div>
        <select id="cDocType" class="input-field">
          <option value="Паспорт РФ" ${curDocType === "Паспорт РФ" ? "selected" : ""}>Паспорт РФ</option>
          <option value="Загранпаспорт РФ" ${curDocType === "Загранпаспорт РФ" ? "selected" : ""}>Загранпаспорт РФ</option>
          <option value="Иностранный документ" ${curDocType === "Иностранный документ" ? "selected" : ""}>Иностранный</option>
        </select>
      </div>
    </div>
    <div class="form-group">
      <div class="form-label">Серия и номер документа (шифруется)</div>
      <input id="cDoc" class="input-field" value="" placeholder="${docPlaceholder}" />
    </div>
    <div style="display:grid;grid-template-columns:1fr 1.2fr;gap:8px;">
      <div class="form-group">
        <div class="form-label">Телефон</div>
        <input id="cPhone" type="tel" class="input-field" value="${comp.phone || ""}" placeholder="+7 999 000-00-00" />
      </div>
      <div class="form-group">
        <div class="form-label">Почта доставки билетов</div>
        <input id="cEmail" type="email" class="input-field" value="${defaultMail}" />
      </div>
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;padding:12px;" onclick="saveCompanion(${comp.id})">Сохранить данные ${comp.name}</button>
  `);
};

window.saveCompanion = async (id) => {
  const name = document.getElementById("cName").value.trim();
  if (!name) return showToast("Укажите имя");
  await fetch("/api/companions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id,
      name,
      full_name: document.getElementById("cFull").value,
      birth_date: (document.getElementById("cBirth") || {}).value || "",
      doc_type: (document.getElementById("cDocType") || {}).value || "Паспорт РФ",
      doc_number: document.getElementById("cDoc").value,
      phone: (document.getElementById("cPhone") || {}).value || "",
      email: (document.getElementById("cEmail") || {}).value || "",
    }),
  });
  closeSheet();
  await loadState();
  showToast(`Данные ${name} сохранены`);
};

window.openCardSheet = () => {
  const s = state.data.settings;
  const curMethod = s.payment_method || "card_3ds";
  openSheet(`
    <div class="director-tag">Локальное шифрование на устройстве</div>
    <h3 style="margin:3px 0 10px;">Карта РФ для автооплаты (до 3D-Secure)</h3>
    <div class="form-group">
      <div class="form-label">Основной режим оплаты</div>
      <select id="vMethod" class="input-field" onchange="document.getElementById('vCardFields').style.display = this.value === 'card_3ds' ? 'block' : 'none'">
        <option value="card_3ds" ${curMethod === "card_3ds" ? "selected" : ""}>Карта РФ — агент сам вводит до пуша / 3D-Secure (100% касс)</option>
        <option value="sbp" ${curMethod === "sbp" ? "selected" : ""}>СБП — пуш / выбор банка на iPhone</option>
      </select>
    </div>
    <div id="vCardFields" style="display:${curMethod === "card_3ds" ? "block" : "none"};">
      <div class="form-group">
        <div class="form-label">Номер карты РФ («Мир» / Visa / MC РФ — шифруется локально)</div>
        <input id="vCard" class="input-field" inputmode="numeric" autocomplete="off" placeholder="${s.card_masked || "2200 0000 0000 0000"}" />
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div class="form-group">
          <div class="form-label">Срок (ММ/ГГ)</div>
          <input id="vExp" class="input-field" placeholder="12/29" autocomplete="off" />
        </div>
        <div class="form-group">
          <div class="form-label">Код CVC (шифруется)</div>
          <input id="vCvc" type="password" inputmode="numeric" maxlength="4" class="input-field" placeholder="•••" autocomplete="off" />
        </div>
      </div>
      <div class="form-group">
        <div class="form-label">Имя на карте (если требуется шлюзом)</div>
        <input id="vHolder" class="input-field" placeholder="IVAN IVANOV" autocomplete="off" />
      </div>
    </div>
    <div class="form-group">
      <div class="form-label">Лимит цены за 1 билет (₽)</div>
      <input id="vMax" class="input-field" type="number" value="${s.max_price_per_ticket}" />
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;padding:12px;" onclick="saveCardVault()">Сохранить карту</button>
  `);
};

window.saveCardVault = async () => {
  const methodEl = document.getElementById("vMethod");
  const cardEl = document.getElementById("vCard");
  const expEl = document.getElementById("vExp");
  const cvcEl = document.getElementById("vCvc");
  const holderEl = document.getElementById("vHolder");
  const res = await fetch("/api/settings/card", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      payment_method: methodEl ? methodEl.value : "card_3ds",
      card_number: cardEl ? cardEl.value : "",
      card_expiry: expEl ? expEl.value : "",
      card_cvc: cvcEl ? cvcEl.value : "",
      card_holder: holderEl ? holderEl.value : "",
      max_price_per_ticket: parseInt(document.getElementById("vMax").value || "60000", 10),
    }),
  });
  if (!res.ok) return showToast("Проверьте номер карты");
  closeSheet();
  await loadState();
  showToast("Карта сохранена в зашифрованном хранилище");
};

window.openMosIdSheet = () => {
  const s = state.data.settings;
  openSheet(`
    <div class="director-tag">Локальное шифрование на устройстве</div>
    <h3 style="margin:3px 0 8px;">Подключить Mos ID (mos.ru)</h3>
    <div class="venue-line" style="line-height:1.42;margin-bottom:10px;">
      Используется агентом только для московских городских театров, чьи квоты продаются через <b>bilet.mos.ru</b> (например, Театр «Около»). Во всех остальных театрах агент покупает напрямую без Mos ID.
    </div>
    <div class="form-group">
      <div class="form-label">Телефон, СНИЛС или почта от mos.ru</div>
      <input id="mosLogin" class="input-field" placeholder="${s.mos_id_masked || "+7 999 000-00-00"}" autocomplete="off" />
    </div>
    <div class="form-group">
      <div class="form-label">Пароль от mos.ru (шифруется локально)</div>
      <input id="mosPwd" type="password" class="input-field" placeholder="••••••••" autocomplete="off" />
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;padding:12px;" onclick="saveMosIdVault()">Сохранить Mos ID</button>
  `);
};

window.saveMosIdVault = async () => {
  const login = ((document.getElementById("mosLogin") || {}).value || "").trim();
  const password = ((document.getElementById("mosPwd") || {}).value || "").trim();
  if (!login) return showToast("Укажите телефон или логин mos.ru");
  const res = await fetch("/api/settings/mos-id", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login, password, ticket_sources_policy: "include_mos_id" }),
  });
  const out = await res.json();
  if (!res.ok) return showToast(out.detail || "Ошибка сохранения Mos ID");
  closeSheet();
  await loadState();
  showToast("Mos ID сохранён");
};

window.startTestCheckoutFromProfile = () => {
  const rep = (state.data && state.data.repertoire) || [];
  const candidate =
    rep.find((p) => (p.status === "want" || p.status === "sniper") && p.upcoming_events && p.upcoming_events.length > 0) ||
    rep.find((p) => p.upcoming_events && p.upcoming_events.length > 0 && p.upcoming_events[0].has_tickets !== false) ||
    rep.find((p) => p.upcoming_events && p.upcoming_events.length > 0);
  if (!candidate) return showToast("Нет ближайших показов для теста");
  openDateEventSheet(candidate.slug, candidate.upcoming_events[0].event_id);
};

window.openSourceSheet = () => {
  openSheet(`
    <h3 style="margin:0 0 10px;">Добавить театр, выставку или блогера</h3>
    <div class="form-group">
      <select id="sKind" class="input-field">
        <option value="telegram">Telegram-канал инфлюенсера / театра</option>
        <option value="instagram">Instagram инфлюенсера / музея</option>
        <option value="theatre_site">Официальный сайт театра</option>
        <option value="exhibition">Музей / Выставочный зал</option>
      </select>
    </div>
    <div class="form-group">
      <input id="sTitle" class="input-field" placeholder="Название (например: ГЭС-2)" />
    </div>
    <div class="form-group">
      <input id="sUrl" class="input-field" placeholder="@handle или https://..." />
    </div>
    <button type="button" class="btn-primary" style="width:100%;margin-top:6px;" onclick="saveSource()">Добавить</button>
  `);
};

window.saveSource = async () => {
  const res = await fetch("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: document.getElementById("sKind").value,
      title: document.getElementById("sTitle").value,
      handle_or_url: document.getElementById("sUrl").value,
    }),
  });
  if (!res.ok) {
    const err = await res.json();
    return showToast(err.detail || "Ошибка");
  }
  closeSheet();
  await loadState();
  showToast("Источник добавлен");
};

window.deleteSource = async (id) => {
  await fetch(`/api/sources/${id}`, { method: "DELETE" });
  await loadState();
};

loadState();
