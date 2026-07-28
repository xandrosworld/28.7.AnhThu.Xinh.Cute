(() => {
  "use strict";

  const state = {
    csrfToken: "",
    user: null,
    inventory: [],
    categories: [],
    users: [],
    products: [],
    partners: [],
    warehouses: [],
  };
  const modalFocus = new WeakMap();

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const page = document.body.dataset.page;
  const role = document.body.dataset.role;

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
    })[char]);

  const formatNumber = (value) => new Intl.NumberFormat("vi-VN").format(value ?? 0);
  const formatDateTime = (value) => {
    if (!value) return "—";
    const normalized = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime())
      ? value
      : new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(date);
  };

  function toast(message, type = "success", title = "") {
    const region = $("#toast-region");
    if (!region) return;
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.setAttribute("role", type === "error" ? "alert" : "status");
    item.innerHTML = `
      <span aria-hidden="true">${type === "error" ? "!" : "✓"}</span>
      <div><strong>${escapeHtml(title || (type === "error" ? "Có lỗi xảy ra" : "Thành công"))}</strong><p>${escapeHtml(message)}</p></div>
      <button type="button" aria-label="Đóng thông báo">×</button>`;
    region.appendChild(item);
    const remove = () => item.remove();
    $("button", item).addEventListener("click", remove);
    window.setTimeout(remove, 4200);
  }

  function setLoading(active) {
    const overlay = $("#loading-overlay");
    if (!overlay) return;
    overlay.classList.toggle("hidden", !active);
    overlay.setAttribute("aria-hidden", String(!active));
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    if (state.csrfToken && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrfToken;
    let response;
    try {
      response = await fetch(path, { credentials: "same-origin", ...options, headers });
    } catch {
      const networkError = new Error("Không thể kết nối máy chủ. Hãy kiểm tra ứng dụng đang chạy.");
      networkError.status = 0;
      throw networkError;
    }
    const raw = await response.json().catch(() => ({ ok: false, message: "Phản hồi máy chủ không hợp lệ." }));
    if (!response.ok) {
      if (response.status === 401 && page !== "login") window.location.href = "/";
      const error = new Error(raw.error?.message || raw.message || "Yêu cầu không thành công.");
      error.status = response.status;
      error.errors = raw.error?.fields || raw.errors || {};
      throw error;
    }
    // The public API uses {data, meta}; legacy aliases remain readable while all
    // pages are migrated. Keeping this normalization here avoids page-specific
    // response assumptions and makes links such as CSV downloads unaffected.
    const nested = raw.data && typeof raw.data === "object" && !Array.isArray(raw.data)
      ? raw.data : {};
    const data = { ...raw, ...nested };
    if (Array.isArray(raw.data) && !data.items) data.items = raw.data;
    data.meta = raw.meta || data.meta || {};
    if (!data.item && nested.id != null) data.item = nested;
    if (!data.user && path.endsWith("/auth/me") && (nested.id != null || nested.username)) data.user = nested;
    data.csrf_token ??= data.meta.csrf_token;
    data.items ??= nested.items || [];
    data.pagination ??= data.meta.pagination || {
      page: 1, pages: 1, per_page: data.items.length || 1, total: data.items.length,
    };
    data.message ??= nested.message || "";
    return data;
  }

  function formData(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  function controlsData(root) {
    return Object.fromEntries($$("input[name], select[name], textarea[name]", root)
      .map((control) => [control.name, control.value]));
  }

  function clearErrors(form) {
    $$(".field-error", form).forEach((element) => { element.textContent = ""; });
    $(".form-alert", form)?.classList.add("hidden");
    $$("input, select, textarea", form).forEach((element) => element.classList.remove("invalid"));
  }

  function showErrors(form, errors = {}) {
    Object.entries(errors).forEach(([field, message]) => {
      const target = $(`[data-error-for="${field}"]`, form);
      const input = $(`[name="${field}"]`, form);
      if (target) target.textContent = message;
      input?.classList.add("invalid");
    });
    const first = $(".invalid", form);
    first?.focus();
  }

  function setButtonBusy(button, busy, text = "Đang xử lý…") {
    if (!button) return;
    if (busy) {
      button.dataset.original = button.innerHTML;
      button.disabled = true;
      button.textContent = text;
    } else {
      button.disabled = false;
      if (button.dataset.original) button.innerHTML = button.dataset.original;
    }
  }

  function openModal(modal) {
    if (!modal) return;
    modalFocus.set(modal, document.activeElement);
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    const focusable = $("input:not([type=hidden]), select, textarea, button", modal);
    window.setTimeout(() => focusable?.focus(), 30);
  }

  function closeModal(modal) {
    if (!modal) return;
    if (modal.id === "scanner-modal") stopScanner();
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    modalFocus.get(modal)?.focus?.();
    modalFocus.delete(modal);
  }

  function initModals() {
    $$(".modal-backdrop").forEach((modal) => {
      $$(".modal-close", modal).forEach((button) => button.addEventListener("click", () => closeModal(modal)));
      modal.addEventListener("mousedown", (event) => {
        if (event.target === modal) closeModal(modal);
      });
    });
    document.addEventListener("keydown", (event) => {
      const openModals = $$(".modal-backdrop:not(.hidden)");
      const topModal = openModals[openModals.length - 1];
      if (event.key === "Escape" && topModal) closeModal(topModal);
      if (event.key === "Tab") {
        const modal = topModal;
        if (!modal) return;
        const focusable = $$('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])', modal)
          .filter((element) => element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0]; const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }

  function pagination(container, data, callback) {
    if (!container) return;
    const current = Number(data?.page) || 1;
    const pages = Math.max(Number(data?.pages) || 1, 1);
    const candidates = [...new Set([1, current - 1, current, current + 1, pages])]
      .filter((number) => number >= 1 && number <= pages).sort((a, b) => a - b);
    container.innerHTML = "";
    const addButton = (label, target, disabled = false, active = false) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `page-button ${active ? "active" : ""}`;
      button.textContent = label;
      button.disabled = disabled;
      if (!disabled) button.addEventListener("click", () => callback(target));
      container.appendChild(button);
    };
    addButton("‹", current - 1, current === 1);
    let previous = 0;
    candidates.forEach((number) => {
      if (previous && number - previous > 1) {
        const dots = document.createElement("span");
        dots.textContent = "…";
        dots.style.padding = "7px 3px";
        container.appendChild(dots);
      }
      addButton(String(number), number, false, number === current);
      previous = number;
    });
    addButton("›", current + 1, current === pages);
  }

  async function initSession() {
    if (page === "login") return;
    const data = await api("/api/auth/me");
    state.csrfToken = data.csrf_token;
    state.user = data.user;
  }

  function initChrome() {
    const sidebar = $("#sidebar");
    const backdrop = $("#sidebar-backdrop");
    const toggle = $("#menu-toggle");
    const closeMenu = () => {
      sidebar?.classList.remove("open");
      backdrop?.classList.remove("show");
      toggle?.setAttribute("aria-expanded", "false");
    };
    toggle?.addEventListener("click", () => {
      const open = sidebar.classList.toggle("open");
      backdrop.classList.toggle("show", open);
      toggle.setAttribute("aria-expanded", String(open));
    });
    backdrop?.addEventListener("click", closeMenu);
    $("#logout-button")?.addEventListener("click", async () => {
      try {
        setLoading(true);
        await api("/api/auth/logout", { method: "POST" });
        window.location.href = "/";
      } catch (error) {
        toast(error.message, "error");
      } finally {
        setLoading(false);
      }
    });
  }

  function initLogin() {
    const form = $("#login-form");
    const toggle = $(".password-toggle");
    toggle?.addEventListener("click", () => {
      const input = $("#password");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      toggle.textContent = show ? "Ẩn" : "Hiện";
      toggle.setAttribute("aria-label", show ? "Ẩn mật khẩu" : "Hiện mật khẩu");
    });
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      clearErrors(form);
      const button = $('button[type="submit"]', form);
      setButtonBusy(button, true, "Đang xác thực…");
      try {
        await api("/api/auth/login", { method: "POST", body: JSON.stringify(formData(form)) });
        window.location.href = "/dashboard";
      } catch (error) {
        showErrors(form, error.errors);
        const alert = $("#login-alert");
        alert.textContent = error.message;
        alert.classList.remove("hidden");
      } finally {
        setButtonBusy(button, false);
      }
    });
  }

  async function initDashboard() {
    $("#today-label").textContent = new Intl.DateTimeFormat("vi-VN", {
      weekday: "long", day: "2-digit", month: "2-digit", year: "numeric",
    }).format(new Date());
    try {
      const data = await api("/api/dashboard");
      const cards = [
        ["blue", "▦", "Mặt hàng", data.summary.products],
        ["green", "◆", "Tổng số lượng", data.summary.total_quantity],
        ["amber", "△", "Sắp thiếu", data.summary.low_stock],
        ["red", "×", "Hết hàng", data.summary.out_of_stock],
        ["blue", "↘", "Nhập kho hôm nay", data.summary.inbound_today],
        ["green", "↗", "Xuất kho hôm nay", data.summary.outbound_today],
      ];
      $("#dashboard-stats").innerHTML = cards.map(([tone, icon, label, value]) => `
        <article class="stat-card ${tone}"><div class="stat-top"><small>${label}</small><span class="stat-icon">${icon}</span></div><strong>${formatNumber(value)}</strong><small>Cập nhật từ cơ sở dữ liệu</small></article>`).join("");

      const max = Math.max(...data.category_distribution.map((entry) => entry.quantity), 1);
      $("#category-chart").innerHTML = data.category_distribution.length
        ? data.category_distribution.map((entry) => `
          <div class="bar-row"><span class="bar-label" title="${escapeHtml(entry.name)}">${escapeHtml(entry.name)}</span><span class="bar-track"><span class="bar-fill" style="width:${Math.max(entry.quantity / max * 100, 2)}%"></span></span><span class="bar-value">${formatNumber(entry.quantity)}</span></div>`).join("")
        : '<div class="empty-state">Chưa có dữ liệu danh mục.</div>';

      $("#recent-activities").innerHTML = data.recent_adjustments.length
        ? data.recent_adjustments.map((entry) => `
          <div class="activity-item"><span class="activity-icon">${entry.difference > 0 ? "+" : "−"}</span><div><strong>${escapeHtml(entry.sku)} · ${escapeHtml(entry.name)}</strong><p>${escapeHtml(entry.full_name)} điều chỉnh ${entry.old_quantity} → ${entry.new_quantity}</p><small>${formatDateTime(entry.created_at)} · ${escapeHtml(entry.reason)}</small></div></div>`).join("")
        : '<div class="empty-state">Chưa có hoạt động điều chỉnh.</div>';
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function loadLookups(form) {
    const data = await api("/api/lookups");
    const category = $('[name="category_id"]', form);
    const warehouse = $('[name="warehouse_id"]', form);
    data.categories.forEach((item) => category.insertAdjacentHTML("beforeend", `<option value="${item.id}">${escapeHtml(item.name)}</option>`));
    data.warehouses.forEach((item) => warehouse.insertAdjacentHTML("beforeend", `<option value="${item.id}">${escapeHtml(item.name)}</option>`));
  }

  function statusBadge(item) {
    return `<span class="badge ${item.status}">${escapeHtml(item.status_label)}</span>`;
  }

  async function loadInventory(targetPage = 1) {
    const form = $("#inventory-filters");
    const params = new URLSearchParams({ ...formData(form), page: targetPage, per_page: 10 });
    [...params.entries()].forEach(([key, value]) => { if (!value) params.delete(key); });
    const body = $("#inventory-body");
    body.innerHTML = '<tr><td colspan="7"><div class="empty-state">Đang tải dữ liệu…</div></td></tr>';
    try {
      const data = await api(`/api/inventory?${params}`);
      state.inventory = data.items;
      body.innerHTML = data.items.length ? data.items.map((item) => `
        <tr>
          <td><span class="sku">${escapeHtml(item.sku)}</span><span class="cell-title">${escapeHtml(item.name)}</span><span class="cell-subtitle">${escapeHtml(item.unit)}</span></td>
          <td>${escapeHtml(item.category_name)}</td>
          <td><span class="cell-title">${escapeHtml(item.warehouse_name)}</span><span class="cell-subtitle">Vị trí ${escapeHtml(item.location || "—")}</span></td>
          <td class="number"><b>${formatNumber(item.quantity)}</b> ${escapeHtml(item.unit)}${item.available_quantity != null ? `<span class="cell-subtitle">Khả dụng: ${formatNumber(item.available_quantity)}</span>` : ""}</td>
          <td>${statusBadge(item)}</td>
          <td>${formatDateTime(item.updated_at)}</td>
          <td><div class="table-actions"><button class="action-button detail-button" data-id="${item.id}" type="button">Chi tiết</button>${["admin", "manager", "warehouse"].includes(role) ? `<button class="action-button adjust-button" data-id="${item.id}" type="button">Kiểm kê</button>` : ""}</div></td>
        </tr>`).join("") : '<tr><td colspan="7"><div class="empty-state">Không tìm thấy hàng hóa phù hợp.</div></td></tr>';
      const start = data.pagination.total ? (data.pagination.page - 1) * data.pagination.per_page + 1 : 0;
      const end = Math.min(data.pagination.page * data.pagination.per_page, data.pagination.total);
      $("#inventory-range").textContent = `Hiển thị ${start}–${end} trong ${data.pagination.total} kết quả`;
      pagination($("#inventory-pagination"), data.pagination, loadInventory);
      $$(".detail-button", body).forEach((button) => button.addEventListener("click", () => showInventoryDetail(button.dataset.id)));
      $$(".adjust-button", body).forEach((button) => button.addEventListener("click", () => openAdjustment(button.dataset.id)));
    } catch (error) {
      body.innerHTML = `<tr><td colspan="7"><div class="empty-state">${escapeHtml(error.message)}</div></td></tr>`;
    }
  }

  async function showInventoryDetail(id) {
    try {
      setLoading(true);
      const data = await api(`/api/inventory/${id}`);
      const item = data.item;
      const movements = Array.isArray(data.movements)
        ? data.movements
        : (Array.isArray(data.adjustments) ? data.adjustments.map((entry) => ({
          ...entry,
          movement_type: "adjustment",
          reference_code: entry.reference_code || `ADJ-${entry.id}`,
          quantity_change: entry.difference,
          balance_after: entry.new_quantity,
        })) : []);
      const movementRows = movements.length ? `
        <div class="table-wrap">
          <table>
            <caption class="sr-only">Lịch sử biến động tồn kho của ${escapeHtml(item.name)}</caption>
            <thead><tr><th>Loại biến động</th><th>Chứng từ</th><th class="number">Thay đổi</th><th class="number">Tồn sau</th><th>Thời gian</th></tr></thead>
            <tbody>${movements.map((entry) => {
              const change = Number(entry.quantity_change ?? 0);
              const differenceClass = change > 0 ? "positive" : change < 0 ? "negative" : "";
              return `<tr>
                <td><span class="badge info">${escapeHtml(statusLabel(entry.movement_type || "adjustment"))}</span>${entry.reason ? `<span class="cell-subtitle">${escapeHtml(entry.reason)}</span>` : ""}</td>
                <td><span class="sku">${escapeHtml(entry.reference_code || "—")}</span></td>
                <td class="number"><span class="difference ${differenceClass}">${change > 0 ? "+" : ""}${formatNumber(change)}</span></td>
                <td class="number">${formatNumber(entry.balance_after ?? 0)} ${escapeHtml(item.unit)}</td>
                <td>${formatDateTime(entry.created_at)}</td>
              </tr>`;
            }).join("")}</tbody>
          </table>
        </div>`
        : '<div class="empty-state" role="status">Chưa có biến động tồn kho.</div>';
      $("#inventory-detail").innerHTML = `
        <div class="detail-hero"><div><span class="sku">${escapeHtml(item.sku)}</span><h3>${escapeHtml(item.name)}</h3></div><div class="detail-quantity"><strong>${formatNumber(item.quantity)}</strong><small>${escapeHtml(item.unit)} hiện có</small></div></div>
        <div class="detail-grid">
          <div class="detail-field"><small>DANH MỤC</small><strong>${escapeHtml(item.category_name)}</strong></div>
          <div class="detail-field"><small>KHO</small><strong>${escapeHtml(item.warehouse_name)}</strong></div>
          <div class="detail-field"><small>VỊ TRÍ</small><strong>${escapeHtml(item.location || "—")}</strong></div>
          <div class="detail-field"><small>TỒN KHẢ DỤNG</small><strong>${formatNumber(item.available_quantity ?? item.quantity)} ${escapeHtml(item.unit)}</strong></div>
          <div class="detail-field"><small>NGƯỠNG TỐI THIỂU</small><strong>${formatNumber(item.min_quantity)} ${escapeHtml(item.unit)}</strong></div>
          <div class="detail-field"><small>TRẠNG THÁI</small><strong>${statusBadge(item)}</strong></div>
          <div class="detail-field"><small>CẬP NHẬT</small><strong>${formatDateTime(item.updated_at)}</strong></div>
        </div>
        <section aria-labelledby="inventory-history-title" aria-live="polite">
          <h3 class="history-title" id="inventory-history-title">Lịch sử biến động tồn kho</h3>
          ${movementRows}
        </section>`;
      openModal($("#inventory-modal"));
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setLoading(false);
    }
  }

  function openAdjustment(id) {
    const item = state.inventory.find((entry) => entry.id === Number(id));
    if (!item) return;
    const form = $("#adjust-form");
    form.reset();
    clearErrors(form);
    form.elements.item_id.value = item.id;
    form.elements.new_quantity.value = item.quantity;
    $("#adjust-summary").innerHTML = `<small>HÀNG HÓA ĐANG KIỂM KÊ</small><strong>${escapeHtml(item.sku)} · ${escapeHtml(item.name)}</strong><span>Hiện có ${formatNumber(item.quantity)} ${escapeHtml(item.unit)} tại ${escapeHtml(item.warehouse_name)}</span>`;
    openModal($("#adjust-modal"));
  }

  async function initInventory() {
    const filters = $("#inventory-filters");
    await loadLookups(filters);
    let debounce;
    filters.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => loadInventory(1), 300); });
    filters.addEventListener("change", () => loadInventory(1));
    $("#inventory-reset").addEventListener("click", () => { filters.reset(); loadInventory(1); });
    $("#adjust-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      clearErrors(form);
      const data = formData(form);
      const button = $('button[type="submit"]', form);
      setButtonBusy(button, true);
      try {
        const result = await api(`/api/inventory/${data.item_id}/adjustments`, {
          method: "POST", body: JSON.stringify({ ...data, new_quantity: Number(data.new_quantity) }),
        });
        closeModal($("#adjust-modal"));
        toast(result.message);
        await loadInventory(1);
      } catch (error) {
        showErrors(form, error.errors);
        toast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });
    await loadInventory();
  }

  async function loadCategories() {
    const search = $("#category-search").value.trim();
    const body = $("#category-body");
    body.innerHTML = '<tr><td colspan="6"><div class="empty-state">Đang tải danh mục…</div></td></tr>';
    try {
      const data = await api(`/api/categories?search=${encodeURIComponent(search)}`);
      state.categories = data.items;
      $("#category-count").textContent = `${data.items.length} danh mục`;
      const canEdit = ["admin", "manager", "cs"].includes(role);
      body.innerHTML = data.items.length ? data.items.map((item) => `
        <tr><td><span class="sku">${escapeHtml(item.code)}</span></td><td><span class="cell-title">${escapeHtml(item.name)}</span></td><td>${escapeHtml(item.description || "—")}</td><td class="number">${formatNumber(item.product_count)}</td><td><span class="badge ${item.status}">${item.status === "active" ? "Hoạt động" : "Ngừng hoạt động"}</span></td><td><div class="table-actions">${canEdit ? `<button class="action-button category-edit" data-id="${item.id}" type="button">Sửa</button>` : ""}${role === "admin" ? `<button class="action-button danger category-delete" data-id="${item.id}" type="button">Xóa</button>` : ""}</div></td></tr>`).join("")
        : '<tr><td colspan="6"><div class="empty-state">Không có danh mục phù hợp.</div></td></tr>';
      $$(".category-edit", body).forEach((button) => button.addEventListener("click", () => editCategory(button.dataset.id)));
      $$(".category-delete", body).forEach((button) => button.addEventListener("click", () => deleteCategory(button.dataset.id)));
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6"><div class="empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-categories" type="button">Thử lại</button></div></td></tr>`;
      $(".retry-categories", body)?.addEventListener("click", loadCategories);
    }
  }

  function editCategory(id) {
    const item = state.categories.find((entry) => entry.id === Number(id));
    const form = $("#category-form");
    form.reset(); clearErrors(form);
    Object.entries(item).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
    $("#category-modal-title").textContent = "Cập nhật danh mục";
    openModal($("#category-modal"));
  }

  async function deleteCategory(id) {
    const item = state.categories.find((entry) => entry.id === Number(id));
    if (!await confirmAction(`Xóa danh mục “${item.name}”? Danh mục đã phát sinh nghiệp vụ sẽ được hệ thống bảo vệ.`, "Xóa danh mục")) return;
    try {
      const result = await api(`/api/categories/${id}`, { method: "DELETE" });
      toast(result.message); await loadCategories();
    } catch (error) { toast(error.message, "error"); }
  }

  async function initCategories() {
    let debounce;
    $("#category-search").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(loadCategories, 250); });
    $("#category-add")?.addEventListener("click", () => {
      const form = $("#category-form"); form.reset(); clearErrors(form); form.elements.id.value = "";
      $("#category-modal-title").textContent = "Thêm danh mục"; openModal($("#category-modal"));
    });
    $("#category-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget; clearErrors(form);
      if (!form.reportValidity()) return;
      const data = formData(form); const id = data.id; delete data.id;
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try {
        const result = await api(id ? `/api/categories/${id}` : "/api/categories", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
        closeModal($("#category-modal")); toast(result.message); await loadCategories();
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    await loadCategories();
  }

  async function loadUsers() {
    const search = $("#user-search").value.trim();
    const body = $("#user-body");
    body.innerHTML = '<tr><td colspan="6"><div class="empty-state">Đang tải người dùng…</div></td></tr>';
    try {
      const data = await api(`/api/users?search=${encodeURIComponent(search)}`);
      state.users = data.items;
      $("#user-count").textContent = `${data.items.length} người dùng`;
      body.innerHTML = data.items.length ? data.items.map((item) => `
        <tr><td><div style="display:flex;align-items:center;gap:9px"><span class="avatar">${escapeHtml(item.avatar_initials)}</span><div><span class="cell-title">${escapeHtml(item.full_name)}</span><span class="cell-subtitle">@${escapeHtml(item.username)}</span></div></div></td>
        <td><span class="cell-title">${escapeHtml(item.email)}</span><span class="cell-subtitle">${escapeHtml(item.phone || "Chưa cập nhật")}</span></td>
        <td><span class="badge info">${escapeHtml(item.role_label)}</span></td><td><span class="badge ${item.status}">${item.status === "active" ? "Hoạt động" : "Đã khóa"}</span></td>
        <td>${formatDateTime(item.created_at)}</td><td><div class="table-actions"><button class="action-button user-edit" data-id="${item.id}" type="button">Sửa</button><button class="action-button danger user-delete" data-id="${item.id}" type="button">Xóa</button></div></td></tr>`).join("")
        : '<tr><td colspan="6"><div class="empty-state">Không có người dùng phù hợp.</div></td></tr>';
      $$(".user-edit", body).forEach((button) => button.addEventListener("click", () => editUser(button.dataset.id)));
      $$(".user-delete", body).forEach((button) => button.addEventListener("click", () => deleteUser(button.dataset.id)));
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6"><div class="empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-users" type="button">Thử lại</button></div></td></tr>`;
      $(".retry-users", body)?.addEventListener("click", loadUsers);
    }
  }

  function editUser(id) {
    const item = state.users.find((entry) => entry.id === Number(id));
    const form = $("#user-form"); form.reset(); clearErrors(form);
    Object.entries(item).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
    form.elements.password.value = ""; $("#password-required").classList.add("hidden");
    form.elements.password.required = false;
    $("#user-modal-title").textContent = "Cập nhật người dùng"; openModal($("#user-modal"));
  }

  async function deleteUser(id) {
    const item = state.users.find((entry) => entry.id === Number(id));
    if (!await confirmAction(`Xóa tài khoản “${item.username}”? Nếu tài khoản có lịch sử nghiệp vụ, hệ thống sẽ từ chối xóa.`, "Xóa tài khoản")) return;
    try { const result = await api(`/api/users/${id}`, { method: "DELETE" }); toast(result.message); await loadUsers(); }
    catch (error) { toast(error.message, "error"); }
  }

  async function initUsers() {
    let debounce;
    $("#user-search").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(loadUsers, 250); });
    $("#user-add").addEventListener("click", () => {
      const form = $("#user-form"); form.reset(); clearErrors(form); form.elements.id.value = "";
      form.elements.password.required = true;
      $("#password-required").classList.remove("hidden"); $("#user-modal-title").textContent = "Thêm người dùng"; openModal($("#user-modal"));
    });
    $("#user-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget; clearErrors(form);
      if (!form.reportValidity()) return;
      const data = formData(form); const id = data.id; delete data.id;
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try {
        const result = await api(id ? `/api/users/${id}` : "/api/users", { method: id ? "PUT" : "POST", body: JSON.stringify(data) });
        closeModal($("#user-modal")); toast(result.message); await loadUsers();
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    await loadUsers();
  }

  function bindProfileForm(selector, endpoint) {
    const form = $(selector);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); clearErrors(form);
      if (!form.reportValidity()) return;
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try {
        const result = await api(endpoint, { method: "PUT", body: JSON.stringify(formData(form)) });
        toast(result.message);
        if (selector === "#password-form") form.reset();
        if (selector === "#profile-form") window.setTimeout(() => window.location.reload(), 700);
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
  }

  function initProfile() {
    bindProfileForm("#profile-form", "/api/profile");
    bindProfileForm("#password-form", "/api/profile/password");
  }

  async function loadAudit(targetPage = 1) {
    try {
      const data = await api(`/api/audit-logs?page=${targetPage}`);
      $("#audit-body").innerHTML = data.items.length ? data.items.map((item) => {
        const details = Object.entries(item.details || {}).slice(0, 3).map(([key, value]) => `${key}: ${value}`).join(" · ");
        return `<tr><td>${formatDateTime(item.created_at)}</td><td><span class="cell-title">${escapeHtml(item.full_name || "Hệ thống")}</span><span class="cell-subtitle">${item.username ? `@${escapeHtml(item.username)}` : ""}</span></td><td><span class="badge info">${escapeHtml(item.action)}</span></td><td>${escapeHtml(item.entity_type)}${item.entity_id ? ` #${item.entity_id}` : ""}</td><td>${escapeHtml(details || "—")}</td><td>${escapeHtml(item.ip_address || "—")}</td></tr>`;
      }).join("") : '<tr><td colspan="6"><div class="empty-state">Chưa có nhật ký hệ thống.</div></td></tr>';
      const start = data.pagination.total ? (data.pagination.page - 1) * data.pagination.per_page + 1 : 0;
      $("#audit-range").textContent = `Hiển thị ${start}–${Math.min(data.pagination.page * data.pagination.per_page, data.pagination.total)} trong ${data.pagination.total} bản ghi`;
      pagination($("#audit-pagination"), data.pagination, loadAudit);
    } catch (error) { toast(error.message, "error"); }
  }

  let operationLookups;
  async function getOperationLookups(refresh = false) {
    if (!operationLookups || refresh) operationLookups = await api("/api/operations/lookups");
    return operationLookups;
  }

  function optionList(items, label, selected = "") {
    return `<option value="">${escapeHtml(label)}</option>${items.map((item) =>
      `<option value="${item.id}" ${String(item.id) === String(selected) ? "selected" : ""}>${escapeHtml(item.sku ? `${item.sku} · ${item.name}` : `${item.code} · ${item.name}`)}</option>`
    ).join("")}`;
  }

  function statusLabel(status) {
    return ({
      draft: "Nháp", pending: "Chờ xác nhận", picking: "Đang lấy hàng",
      completed: "Hoàn tất", rejected: "Từ chối", cancelled: "Đã hủy",
      active: "Hoạt động", inactive: "Ngừng hoạt động",
      inbound: "Nhập kho", outbound: "Xuất kho", stocktake: "Kiểm kê",
      adjustment: "Điều chỉnh",
    })[status] || status;
  }

  function confirmAction(message, acceptLabel = "Xác nhận") {
    return new Promise((resolve) => {
      let modal = $("#global-confirm");
      if (!modal) {
        modal = document.createElement("div");
        modal.id = "global-confirm";
        modal.className = "modal-backdrop hidden";
        modal.innerHTML = `<section class="modal modal-small" role="alertdialog" aria-modal="true" aria-labelledby="global-confirm-title">
          <div class="modal-header"><div><span class="eyebrow">XÁC NHẬN</span><h2 id="global-confirm-title">Xác nhận thao tác</h2></div></div>
          <p class="confirm-copy"></p><div class="modal-actions"><button class="button secondary confirm-cancel" type="button">Quay lại</button><button class="button danger-button confirm-accept" type="button"></button></div></section>`;
        document.body.appendChild(modal);
      }
      $(".confirm-copy", modal).textContent = message;
      $(".confirm-accept", modal).textContent = acceptLabel;
      openModal(modal);
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        closeModal(modal);
        resolve(value);
      };
      $(".confirm-cancel", modal).onclick = () => finish(false);
      $(".confirm-accept", modal).onclick = () => finish(true);
      modal.onmousedown = (event) => { if (event.target === modal) finish(false); };
    });
  }

  function printSection(kind) {
    document.body.classList.add(`print-${kind}`);
    const cleanup = () => {
      document.body.classList.remove(`print-${kind}`);
      window.removeEventListener("afterprint", cleanup);
    };
    window.addEventListener("afterprint", cleanup);
    window.print();
  }

  async function initProducts() {
    const lookups = await getOperationLookups();
    const filters = $("#product-filters");
    $$('[name="category_id"]', document).forEach((select) => {
      const current = select.value;
      api("/api/lookups").then((data) => {
        select.innerHTML = optionList(data.categories, "Chọn danh mục", current);
      }).catch(() => {});
    });
    $$('[name="warehouse_id"]', document).forEach((select) => {
      select.innerHTML = optionList(lookups.warehouses, "Chọn kho", select.value);
    });
    $$('[name="unit_id"]', document).forEach((select) => {
      select.innerHTML = optionList(lookups.units || [], "Chọn đơn vị tính", select.value);
    });
    const load = async () => {
      const query = new URLSearchParams(formData(filters));
      const body = $("#product-body");
      body.innerHTML = '<tr><td colspan="7"><div class="empty-state">Đang tải hàng hóa…</div></td></tr>';
      try {
        const data = await api(`/api/products?${query}`);
        state.products = data.items;
        body.innerHTML = data.items.length ? data.items.map((item) => `<tr>
          <td><span class="sku">${escapeHtml(item.sku)}</span><span class="cell-subtitle">${escapeHtml(item.barcode || "Chưa có barcode")}</span></td>
          <td><span class="cell-title">${escapeHtml(item.name)}</span></td><td>${escapeHtml(item.category_name)}</td>
          <td>${escapeHtml(item.unit)}</td><td><span class="cell-title">${escapeHtml(item.warehouse_name)}</span><span class="cell-subtitle">${escapeHtml(item.location || "Chưa xếp vị trí")}</span></td>
          <td><span class="badge ${item.status}">${statusLabel(item.status)}</span></td>
          <td>${["admin", "manager", "cs"].includes(role) ? `<div class="table-actions"><button class="action-button product-edit" data-id="${item.id}" type="button">Sửa</button><button class="action-button ${item.status === "active" ? "danger" : ""} product-toggle" data-id="${item.id}" type="button">${item.status === "active" ? "Ngừng" : "Kích hoạt"}</button></div>` : ""}</td></tr>`).join("")
          : '<tr><td colspan="7"><div class="empty-state">Không tìm thấy hàng hóa phù hợp.</div></td></tr>';
        $$(".product-edit", body).forEach((button) => button.addEventListener("click", () => {
          const item = state.products.find((entry) => entry.id === Number(button.dataset.id));
          if (!item) return;
          const form = $("#product-form"); form.reset(); clearErrors(form);
          Object.entries(item).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
          $("#product-modal-title").textContent = "Cập nhật hàng hóa";
          openModal($("#product-modal"));
        }));
        $$(".product-toggle", body).forEach((button) => button.addEventListener("click", async () => {
          const item = state.products.find((entry) => entry.id === Number(button.dataset.id));
          const next = item.status === "active" ? "inactive" : "active";
          if (!await confirmAction(`${next === "inactive" ? "Ngừng" : "Kích hoạt"} hàng hóa “${item.name}”?`, next === "inactive" ? "Ngừng hoạt động" : "Kích hoạt")) return;
          try {
            const result = await api(`/api/products/${item.id}`, { method: "PUT", body: JSON.stringify({ ...item, status: next }) });
            toast(result.message); operationLookups = null; await load();
          } catch (error) { toast(error.message, "error"); }
        }));
      } catch (error) {
        body.innerHTML = `<tr><td colspan="7"><div class="empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-products" type="button">Thử lại</button></div></td></tr>`;
        $(".retry-products")?.addEventListener("click", load);
      }
    };
    let timer;
    filters.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    filters.addEventListener("change", load);
    filters.addEventListener("reset", () => window.setTimeout(load));
    $("#product-add")?.addEventListener("click", () => {
      $("#product-form").reset(); clearErrors($("#product-form")); $("#product-form").elements.id.value = "";
      $("#product-modal-title").textContent = "Thêm hàng hóa"; openModal($("#product-modal"));
    });
    $("#product-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      clearErrors(form);
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try {
        const payload = formData(form); const id = payload.id; delete payload.id;
        const result = await api(id ? `/api/products/${id}` : "/api/products", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
        closeModal($("#product-modal")); toast(result.message); operationLookups = null; await load();
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    initScanner();
    await load();
  }

  async function initPartners() {
    const root = $("[data-partner-type]");
    const type = root.dataset.partnerType;
    const search = $("#partner-search");
    const load = async () => {
      const body = $("#partner-body");
      body.innerHTML = '<tr><td colspan="6"><div class="empty-state">Đang tải đối tác…</div></td></tr>';
      try {
        const data = await api(`/api/${type}?search=${encodeURIComponent(search.value.trim())}`);
        state.partners = data.items;
        $("#partner-count").textContent = `${data.items.length} đối tác`;
        body.innerHTML = data.items.length ? data.items.map((item) => `<tr><td><span class="sku">${escapeHtml(item.code)}</span></td>
          <td><span class="cell-title">${escapeHtml(item.name)}</span></td><td><span class="cell-title">${escapeHtml(item.email || "—")}</span><span class="cell-subtitle">${escapeHtml(item.phone || "Chưa có số điện thoại")}</span></td>
          <td>${escapeHtml(type === "customers" ? item.contract_emails : item.address || "—")}</td><td><span class="badge ${item.status}">${statusLabel(item.status)}</span></td>
          <td>${["admin", "manager", "cs"].includes(role) ? `<div class="table-actions"><button class="action-button partner-edit" data-id="${item.id}" type="button">Sửa</button><button class="action-button ${item.status === "active" ? "danger" : ""} partner-toggle" data-id="${item.id}" type="button">${item.status === "active" ? "Ngừng" : "Kích hoạt"}</button></div>` : ""}</td></tr>`).join("")
          : '<tr><td colspan="6"><div class="empty-state">Chưa có đối tác phù hợp.</div></td></tr>';
        $$(".partner-edit", body).forEach((button) => button.addEventListener("click", () => {
          const item = state.partners.find((entry) => entry.id === Number(button.dataset.id));
          const form = $("#partner-form"); form.reset(); clearErrors(form);
          Object.entries(item || {}).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
          $("#partner-modal-title").textContent = `Cập nhật ${type === "customers" ? "khách hàng" : "nhà cung cấp"}`;
          openModal($("#partner-modal"));
        }));
        $$(".partner-toggle", body).forEach((button) => button.addEventListener("click", async () => {
          const item = state.partners.find((entry) => entry.id === Number(button.dataset.id));
          const next = item.status === "active" ? "inactive" : "active";
          if (!await confirmAction(`${next === "inactive" ? "Ngừng" : "Kích hoạt"} đối tác “${item.name}”?`, next === "inactive" ? "Ngừng hoạt động" : "Kích hoạt")) return;
          try {
            const result = await api(`/api/${type}/${item.id}`, { method: "PUT", body: JSON.stringify({ ...item, status: next }) });
            toast(result.message); operationLookups = null; await load();
          } catch (error) { toast(error.message, "error"); }
        }));
      } catch (error) {
        body.innerHTML = `<tr><td colspan="6"><div class="empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-partners" type="button">Thử lại</button></div></td></tr>`;
        $(".retry-partners", body)?.addEventListener("click", load);
      }
    };
    let timer;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    $("#partner-add")?.addEventListener("click", () => {
      $("#partner-form").reset(); clearErrors($("#partner-form")); $("#partner-form").elements.id.value = "";
      $("#partner-modal-title").textContent = `Thêm ${type === "customers" ? "khách hàng" : "nhà cung cấp"}`;
      openModal($("#partner-modal"));
    });
    $("#partner-form")?.addEventListener("submit", async (event) => {
      event.preventDefault(); const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const button = $('button[type="submit"]', form); setButtonBusy(button, true); clearErrors(form);
      try {
        const payload = formData(form); const id = payload.id; delete payload.id;
        const result = await api(id ? `/api/${type}/${id}` : `/api/${type}`, { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
        closeModal($("#partner-modal")); toast(result.message); operationLookups = null; await load();
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    await load();
  }

  async function initWarehouses() {
    const grid = $("#warehouse-grid");
    const load = async () => {
      try {
        const data = await api("/api/warehouses");
        state.warehouses = data.items;
        grid.innerHTML = data.items.length ? data.items.map((item) => `<article class="panel warehouse-card">
          <div class="warehouse-symbol" aria-hidden="true">▣</div><div><span class="sku">${escapeHtml(item.code)}</span><h2>${escapeHtml(item.name)}</h2><p>${escapeHtml(item.address || "Chưa cập nhật địa chỉ")}</p></div>
          <dl><div><dt>Mặt hàng</dt><dd>${formatNumber(item.product_count)}</dd></div><div><dt>Tổng số lượng</dt><dd>${formatNumber(item.total_quantity)}</dd></div></dl>
          <div class="card-actions"><span class="badge ${item.status}">${statusLabel(item.status)}</span>${role === "admin" ? `<button class="action-button warehouse-edit" data-id="${item.id}" type="button">Sửa</button><button class="action-button ${item.status === "active" ? "danger" : ""} warehouse-toggle" data-id="${item.id}" type="button">${item.status === "active" ? "Ngừng" : "Kích hoạt"}</button>` : ""}</div></article>`).join("")
          : '<div class="panel empty-state">Chưa có kho hàng.</div>';
        $$(".warehouse-edit", grid).forEach((button) => button.addEventListener("click", () => {
          const item = state.warehouses.find((entry) => entry.id === Number(button.dataset.id));
          const form = $("#warehouse-form"); form.reset(); clearErrors(form);
          Object.entries(item || {}).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
          $("#warehouse-modal-title").textContent = "Cập nhật kho"; openModal($("#warehouse-modal"));
        }));
        $$(".warehouse-toggle", grid).forEach((button) => button.addEventListener("click", async () => {
          const item = state.warehouses.find((entry) => entry.id === Number(button.dataset.id));
          const next = item.status === "active" ? "inactive" : "active";
          if (!await confirmAction(`${next === "inactive" ? "Ngừng" : "Kích hoạt"} kho “${item.name}”?`, next === "inactive" ? "Ngừng hoạt động" : "Kích hoạt")) return;
          try {
            const result = await api(`/api/warehouses/${item.id}`, { method: "PUT", body: JSON.stringify({ ...item, status: next }) });
            toast(result.message); operationLookups = null; await load();
          } catch (error) { toast(error.message, "error"); }
        }));
      } catch (error) {
        grid.innerHTML = `<div class="panel empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-warehouses" type="button">Thử lại</button></div>`;
        $(".retry-warehouses", grid)?.addEventListener("click", load);
      }
    };
    $("#warehouse-add")?.addEventListener("click", () => {
      const form = $("#warehouse-form"); form.reset(); clearErrors(form); form.elements.id.value = "";
      $("#warehouse-modal-title").textContent = "Thêm kho"; openModal($("#warehouse-modal"));
    });
    $("#warehouse-form")?.addEventListener("submit", async (event) => {
      event.preventDefault(); const form = event.currentTarget;
      if (!form.reportValidity()) return;
      clearErrors(form); const payload = formData(form); const id = payload.id; delete payload.id;
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try {
        const result = await api(id ? `/api/warehouses/${id}` : "/api/warehouses", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
        closeModal($("#warehouse-modal")); toast(result.message); operationLookups = null; await load();
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    await load();
  }

  let scannerStream;
  let scannerTimer;
  let scannerTarget;
  async function stopScanner() {
    clearInterval(scannerTimer);
    scannerStream?.getTracks().forEach((track) => track.stop());
    scannerStream = null;
    const video = $("#scanner-video");
    if (video) video.srcObject = null;
  }
  async function openScanner(targetId) {
    scannerTarget = document.getElementById(targetId);
    const modal = $("#scanner-modal");
    $("#scanner-manual").value = "";
    openModal(modal);
    const help = $("#scanner-help");
    help.textContent = "Hướng camera vào barcode. Nếu trình duyệt không hỗ trợ, dùng máy quét USB hoặc nhập tay bên dưới.";
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices?.getUserMedia) {
      help.textContent = "Camera barcode chưa được trình duyệt hỗ trợ. Hãy dùng máy quét USB hoặc nhập mã thủ công.";
      $("#scanner-manual").focus();
      return;
    }
    try {
      scannerStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      const video = $("#scanner-video");
      video.srcObject = scannerStream; await video.play();
      const detector = new BarcodeDetector({ formats: ["code_128", "ean_13", "ean_8", "qr_code"] });
      scannerTimer = window.setInterval(async () => {
        try {
          const codes = await detector.detect(video);
          if (codes[0]?.rawValue) applyScan(codes[0].rawValue);
        } catch { /* camera may be between frames */ }
      }, 450);
    } catch {
      help.textContent = "Không thể mở camera. Kiểm tra quyền camera hoặc dùng máy quét USB / nhập thủ công.";
      $("#scanner-manual").focus();
    }
  }
  function applyScan(value) {
    if (!value.trim() || !scannerTarget) return;
    scannerTarget.value = value.trim();
    scannerTarget.dispatchEvent(new Event("change", { bubbles: true }));
    stopScanner(); closeModal($("#scanner-modal")); toast(`Đã nhận barcode ${value.trim()}.`);
  }
  function initScanner() {
    $$(".scan-trigger").forEach((button) => button.addEventListener("click", () => openScanner(button.dataset.target)));
    $("#scanner-apply")?.addEventListener("click", () => applyScan($("#scanner-manual").value));
    $("#scanner-manual")?.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); applyScan(event.currentTarget.value); } });
    $$(".scanner-close").forEach((button) => button.addEventListener("click", stopScanner));
  }

  function receiptLine(lookups, index, item = {}) {
    return `<tr class="line-row"><td><select name="inventory_id" required aria-label="Hàng hóa dòng ${index + 1}">${optionList(lookups.products, "Chọn hàng hóa", item.inventory_id)}</select></td>
      <td><input name="quantity" type="number" min="0.001" step="0.001" value="${escapeHtml(item.quantity || "")}" required aria-label="Số lượng dòng ${index + 1}"></td>
      <td><input name="pallet_id" maxlength="50" value="${escapeHtml(item.pallet_id || "")}" aria-label="Pallet dòng ${index + 1}"></td>
      <td><div class="input-action"><input id="line-barcode-${index}" name="barcode" maxlength="50" value="${escapeHtml(item.barcode || "")}" aria-label="Barcode dòng ${index + 1}"><button class="icon-button scan-trigger" type="button" data-target="line-barcode-${index}" aria-label="Quét barcode">⌗</button></div></td>
      <td><input name="expiry_date" type="date" value="${escapeHtml(item.expiry_date || "")}" aria-label="Hạn dùng dòng ${index + 1}"></td>
      <td><button class="action-button danger line-remove" type="button" aria-label="Xóa dòng">×</button></td></tr>`;
  }

  async function initReceipts() {
    const root = $("[data-receipt-type]");
    const type = root.dataset.receiptType;
    const endpoint = `/api/${type}-receipts`;
    const lookups = await getOperationLookups();
    const form = $("#receipt-form");
    let currentReceipt = null;
    let lineSerial = 0;
    form.elements.partner_id.innerHTML = optionList(type === "inbound" ? lookups.suppliers : lookups.customers, "Chọn đối tác");
    form.elements.warehouse_id.innerHTML = optionList(lookups.warehouses, "Chọn kho");
    const addLine = (item = {}) => {
      const index = lineSerial++;
      const warehouseId = Number(form.elements.warehouse_id.value);
      const scopedLookups = { ...lookups, products: warehouseId ? lookups.products.filter((product) => Number(product.warehouse_id) === warehouseId) : lookups.products };
      $("#line-body").insertAdjacentHTML("beforeend", receiptLine(scopedLookups, index, item));
      const row = $("#line-body tr:last-child");
      $(".line-remove", row).addEventListener("click", () => row.remove());
      $(".scan-trigger", row).addEventListener("click", (event) => openScanner(event.currentTarget.dataset.target));
    };
    form.elements.warehouse_id.addEventListener("change", () => {
      const warehouseId = Number(form.elements.warehouse_id.value);
      $$(".line-row", form).forEach((row) => {
        const select = $('[name="inventory_id"]', row); const selected = select.value;
        const products = warehouseId ? lookups.products.filter((product) => Number(product.warehouse_id) === warehouseId) : lookups.products;
        select.innerHTML = optionList(products, "Chọn hàng hóa", selected);
      });
    });
    $("#line-add").addEventListener("click", addLine);
    const load = async () => {
      const query = new URLSearchParams(formData($("#receipt-filters")));
      if ($("#receipt-filters").dataset.page) query.set("page", $("#receipt-filters").dataset.page);
      const body = $("#receipt-body");
      body.innerHTML = '<tr><td colspan="7"><div class="empty-state">Đang tải chứng từ…</div></td></tr>';
      try {
        const data = await api(`${endpoint}?${query}`);
        const stats = [["draft", "Nháp"], ["pending", "Chờ xử lý"], ["picking", "Đang lấy"], ["completed", "Hoàn tất"]];
        $("#receipt-stats").innerHTML = stats.map(([key, label]) => `<article><span class="badge ${key}">${label}</span><strong>${formatNumber(data.stats[key] || 0)}</strong></article>`).join("");
        body.innerHTML = data.items.length ? data.items.map((item) => `<tr><td><span class="sku">${escapeHtml(item.code)}</span></td><td><span class="cell-title">${escapeHtml(item.partner_name)}</span>${item.request_email ? `<span class="cell-subtitle">${escapeHtml(item.request_email)}</span>` : ""}</td>
          <td>${escapeHtml(item.warehouse_name)}</td><td><b>${formatNumber(item.item_count)}</b> mặt hàng<span class="cell-subtitle">${formatNumber(item.total_quantity)} đơn vị</span></td>
          <td><span class="badge ${item.status}">${statusLabel(item.status)}</span></td><td>${formatDateTime(item.created_at)}</td><td><button class="action-button receipt-view" data-id="${item.id}" type="button">Chi tiết</button></td></tr>`).join("")
          : '<tr><td colspan="7"><div class="empty-state">Chưa có phiếu phù hợp.</div></td></tr>';
        $$(".receipt-view").forEach((button) => button.addEventListener("click", () => showReceipt(button.dataset.id)));
        const pg = data.pagination;
        if (pg?.total != null) {
          const start = pg.total ? (pg.page - 1) * pg.per_page + 1 : 0;
          $("#receipt-range").textContent = `Hiển thị ${start}–${Math.min(pg.page * pg.per_page, pg.total)} trong ${pg.total} phiếu`;
          pagination($("#receipt-pagination"), pg, (target) => {
            $("#receipt-filters").dataset.page = target;
            load();
          });
        } else {
          $("#receipt-range").textContent = `${data.items.length} phiếu`;
          $("#receipt-pagination").innerHTML = "";
        }
      } catch (error) {
        body.innerHTML = `<tr><td colspan="7"><div class="empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-receipts" type="button">Thử lại</button></div></td></tr>`;
        $(".retry-receipts", body)?.addEventListener("click", load);
      }
    };
    async function showReceipt(id) {
      try {
        setLoading(true);
        const data = await api(`${endpoint}/${id}`); const item = data.item;
        currentReceipt = item;
        $("#receipt-detail-modal").dataset.id = id;
        $("#receipt-detail").innerHTML = `<div class="document-head"><div><span class="brand-mark">DN</span><p>DNP LOGISTICS<br><small>Warehouse Management System</small></p></div><div><span class="eyebrow">${type === "inbound" ? "PHIẾU NHẬP KHO" : "PHIẾU XUẤT KHO"}</span><h2>${escapeHtml(item.code)}</h2></div></div>
          <div class="detail-grid"><div class="detail-field"><small>ĐỐI TÁC</small><strong>${escapeHtml(item.partner_name)}</strong></div><div class="detail-field"><small>KHO</small><strong>${escapeHtml(item.warehouse_name)}</strong></div><div class="detail-field"><small>TRẠNG THÁI</small><strong><span class="badge ${item.status}">${statusLabel(item.status)}</span></strong></div><div class="detail-field"><small>NGƯỜI LẬP</small><strong>${escapeHtml(item.created_by_name)}</strong></div>${item.request_email ? `<div class="detail-field"><small>EMAIL YÊU CẦU</small><strong>${escapeHtml(item.request_email)}</strong></div>` : ""}${item.container_no ? `<div class="detail-field"><small>CONTAINER / SEAL</small><strong>${escapeHtml(item.container_no)} · ${escapeHtml(item.seal_no || "—")}</strong></div>` : ""}</div>
          <div class="table-wrap"><table><thead><tr><th>SKU</th><th>Hàng hóa</th><th>Pallet</th><th>Barcode</th><th class="number">Chứng từ</th>${type === "inbound" ? '<th class="number">Thực nhận</th><th>Vấn đề</th>' : ""}<th>Đơn vị</th></tr></thead><tbody>${item.items.map((line) => `<tr><td><span class="sku">${escapeHtml(line.sku)}</span></td><td>${escapeHtml(line.name)}</td><td>${escapeHtml(line.pallet_id || "—")}</td><td>${escapeHtml(line.barcode || "—")}</td><td class="number"><b>${formatNumber(line.quantity)}</b></td>${type === "inbound" ? `<td class="number"><b>${formatNumber(line.accepted_quantity)}</b></td><td>${escapeHtml(line.issue_note || "—")}</td>` : ""}<td>${escapeHtml(line.unit)}</td></tr>`).join("")}</tbody></table></div>
          <div class="signature-grid"><div>Người lập phiếu<br><small>(Ký, ghi rõ họ tên)</small></div><div>Nhân viên kho<br><small>(Ký, ghi rõ họ tên)</small></div><div>Người giao/nhận<br><small>(Ký, ghi rõ họ tên)</small></div></div>`;
        const canConfirm = type === "inbound" ? item.status === "pending" : ["pending", "picking"].includes(item.status);
        $("#receipt-confirm").classList.toggle("hidden", !canConfirm);
        $("#receipt-cancel")?.classList.toggle("hidden", !["draft", "pending", "picking"].includes(item.status));
        $("#receipt-edit")?.classList.toggle("hidden", item.status !== "draft");
        $("#receipt-inspect")?.classList.toggle("hidden", !["draft", "pending"].includes(item.status));
        $("#receipt-check-stock")?.classList.toggle("hidden", !["draft", "pending", "picking"].includes(item.status));
        $("#receipt-picking")?.classList.toggle("hidden", !["pending", "picking"].includes(item.status));
        $("#receipt-start-picking")?.classList.toggle("hidden", item.status !== "pending");
        $("#receipt-reject")?.classList.toggle("hidden", !["pending", "picking"].includes(item.status));
        openModal($("#receipt-detail-modal"));
      } catch (error) { toast(error.message, "error"); } finally { setLoading(false); }
    }
    $("#receipt-add")?.addEventListener("click", () => {
      form.reset(); clearErrors(form); form.elements.id.value = "";
      $("#line-body").innerHTML = ""; addLine(); $("#receipt-modal-title").textContent = `Tạo phiếu ${type === "inbound" ? "nhập kho" : "xuất kho"}`; openModal($("#receipt-modal"));
    });
    $("#receipt-edit")?.addEventListener("click", () => {
      if (!currentReceipt || currentReceipt.status !== "draft") return;
      form.reset(); clearErrors(form);
      const values = { ...currentReceipt, partner_id: currentReceipt.partner_id, id: currentReceipt.id };
      Object.entries(values).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
      $("#line-body").innerHTML = ""; currentReceipt.items.forEach((item) => addLine(item));
      $("#receipt-modal-title").textContent = `Cập nhật phiếu ${type === "inbound" ? "nhập kho" : "xuất kho"}`;
      closeModal($("#receipt-detail-modal")); openModal($("#receipt-modal"));
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (!form.reportValidity()) return; clearErrors(form);
      const payload = formData(form);
      payload.items = $$(".line-row", form).map(controlsData);
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try {
        const id = payload.id; delete payload.id;
        const result = await api(id ? `${endpoint}/${id}` : endpoint, { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
        closeModal($("#receipt-modal")); toast(result.message); await load();
      } catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    $("#receipt-confirm")?.addEventListener("click", async () => {
      if (!await confirmAction("Xác nhận phiếu sẽ cập nhật tồn kho và không thể thực hiện lần thứ hai.", "Xác nhận phiếu")) return;
      try {
        setLoading(true);
        const result = await api(`${endpoint}/${$("#receipt-detail-modal").dataset.id}/confirm`, { method: "POST" });
        closeModal($("#receipt-detail-modal")); toast(result.message); await load();
      } catch (error) { toast(error.message, "error"); } finally { setLoading(false); }
    });
    $("#receipt-inspect")?.addEventListener("click", () => {
      if (!currentReceipt) return;
      $("#inspection-body").innerHTML = currentReceipt.items.map((line) => `<tr class="inspection-line" data-id="${line.id}"><td><span class="sku">${escapeHtml(line.sku)}</span><span class="cell-title">${escapeHtml(line.name)}</span></td><td class="number">${formatNumber(line.quantity)} ${escapeHtml(line.unit)}</td><td><input name="accepted_quantity" type="number" min="0" max="${line.quantity}" step="0.001" value="${line.accepted_quantity ?? line.quantity}" required aria-label="Thực nhận ${escapeHtml(line.name)}"></td><td><input name="issue_note" maxlength="300" value="${escapeHtml(line.issue_note || "")}" placeholder="Thiếu, hỏng hoặc bị từ chối"></td></tr>`).join("");
      openModal($("#inspection-modal"));
    });
    $("#inspection-form")?.addEventListener("submit", async (event) => {
      event.preventDefault(); const inspectForm = event.currentTarget;
      if (!inspectForm.reportValidity()) return;
      clearErrors(inspectForm);
      const items = $$(".inspection-line", inspectForm).map((line) => ({
        id: Number(line.dataset.id),
        accepted_quantity: Number($('[name="accepted_quantity"]', line).value),
        issue_note: $('[name="issue_note"]', line).value,
      }));
      const button = $('button[type="submit"]', inspectForm); setButtonBusy(button, true);
      try {
        const result = await api(`${endpoint}/${currentReceipt.id}/inspect`, { method: "POST", body: JSON.stringify({ items }) });
        closeModal($("#inspection-modal")); closeModal($("#receipt-detail-modal")); toast(result.message); await load();
      } catch (error) { showErrors(inspectForm, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    $("#receipt-check-stock")?.addEventListener("click", async () => {
      try {
        const result = await api(`${endpoint}/${$("#receipt-detail-modal").dataset.id}/check-stock`);
        $("#stock-check-result")?.remove();
        $("#receipt-detail").insertAdjacentHTML("beforeend", `<section class="picking-section" id="stock-check-result"><h3>KẾT QUẢ KIỂM TRA TỒN</h3><div class="table-wrap"><table><thead><tr><th>Hàng hóa</th><th class="number">Yêu cầu</th><th class="number">Khả dụng</th><th>Kết quả</th></tr></thead><tbody>${result.items.map((item) => `<tr><td><span class="sku">${escapeHtml(item.sku)}</span><span class="cell-title">${escapeHtml(item.name)}</span></td><td class="number">${formatNumber(item.requested)}</td><td class="number">${formatNumber(item.available)}</td><td><span class="badge ${item.sufficient ? "completed" : "rejected"}">${item.sufficient ? "Đủ tồn" : "Thiếu tồn"}</span></td></tr>`).join("")}</tbody></table></div></section>`);
        toast(result.message, result.sufficient ? "success" : "error", "Kiểm tra tồn kho");
      } catch (error) { toast(error.message, "error"); }
    });
    $("#receipt-picking")?.addEventListener("click", async () => {
      try {
        const result = await api(`${endpoint}/${$("#receipt-detail-modal").dataset.id}/picking-list`);
        $("#picking-result")?.remove();
        $("#receipt-detail").insertAdjacentHTML("beforeend", `<section class="picking-section" id="picking-result"><h3>PICKING LIST · ${escapeHtml(result.receipt_code)}</h3><p>${escapeHtml(result.strategy)}</p><ol>${result.items.map((item) => `<li><b>${escapeHtml(item.sku)}</b> · ${escapeHtml(item.name)} — ${formatNumber(item.quantity)} ${escapeHtml(item.unit)} · Pallet ${escapeHtml(item.pallet_id || "FIFO")}</li>`).join("")}</ol></section>`);
        toast("Đã tạo picking list. Chọn In phiếu để in.", "success");
      } catch (error) { toast(error.message, "error"); }
    });
    $("#receipt-cancel")?.addEventListener("click", async () => {
      if (!await confirmAction("Hủy phiếu đang chọn? Phiếu đã hoàn tất sẽ không thể hủy.", "Hủy phiếu")) return;
      try {
        const result = await api(`${endpoint}/${$("#receipt-detail-modal").dataset.id}/cancel`, { method: "POST" });
        closeModal($("#receipt-detail-modal")); toast(result.message); await load();
      } catch (error) { toast(error.message, "error"); }
    });
    $("#receipt-print").addEventListener("click", () => printSection("receipt"));
    let timer;
    const resetPageAndLoad = () => { delete $("#receipt-filters").dataset.page; load(); };
    $("#receipt-filters").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(resetPageAndLoad, 250); });
    $("#receipt-filters").addEventListener("change", resetPageAndLoad);
    $("#receipt-filters").addEventListener("reset", () => setTimeout(resetPageAndLoad));
    initScanner(); await load();
  }

  function stocktakeLine(lookups, index) {
    return `<tr class="stocktake-line"><td><select name="inventory_id" required aria-label="Hàng hóa dòng ${index + 1}">${optionList(lookups.products, "Chọn hàng hóa")}</select></td>
      <td class="system-quantity">—</td><td><input name="counted_quantity" type="number" min="0" step="0.001" required aria-label="Thực đếm"></td><td><input name="reason" maxlength="200" placeholder="Bắt buộc nếu có chênh lệch" aria-label="Lý do"></td><td><button class="action-button danger line-remove" type="button" aria-label="Xóa dòng">×</button></td></tr>`;
  }

  async function initStocktakes() {
    const lookups = await getOperationLookups();
    const form = $("#stocktake-form");
    let currentStocktake = null;
    form.elements.warehouse_id.innerHTML = optionList(lookups.warehouses, "Chọn kho");
    const addLine = () => {
      const index = $$(".stocktake-line").length;
      const warehouseId = Number(form.elements.warehouse_id.value);
      const scopedLookups = { ...lookups, products: warehouseId ? lookups.products.filter((item) => Number(item.warehouse_id) === warehouseId) : lookups.products };
      $("#stocktake-line-body").insertAdjacentHTML("beforeend", stocktakeLine(scopedLookups, index));
      const row = $("#stocktake-line-body tr:last-child");
      $('[name="inventory_id"]', row).addEventListener("change", (event) => {
        const product = lookups.products.find((item) => item.id === Number(event.target.value));
        $(".system-quantity", row).textContent = product ? `${formatNumber(product.quantity)} ${product.unit}` : "—";
        row.dataset.systemQuantity = product?.quantity ?? "";
      });
      $(".line-remove", row).addEventListener("click", () => row.remove());
    };
    form.elements.warehouse_id.addEventListener("change", () => {
      const warehouseId = Number(form.elements.warehouse_id.value);
      const products = warehouseId ? lookups.products.filter((item) => Number(item.warehouse_id) === warehouseId) : lookups.products;
      $$(".stocktake-line", form).forEach((row) => {
        const select = $('[name="inventory_id"]', row);
        select.innerHTML = optionList(products, "Chọn hàng hóa", select.value);
        select.dispatchEvent(new Event("change"));
      });
    });
    $("#stocktake-line-add").addEventListener("click", addLine);
    const load = async () => {
      try {
        const data = await api(`/api/stocktakes?search=${encodeURIComponent($("#stocktake-search").value)}`);
        $("#stocktake-body").innerHTML = data.items.length ? data.items.map((item) => `<tr><td><span class="sku">${escapeHtml(item.code)}</span></td><td>${escapeHtml(item.warehouse_name)}</td><td>${formatNumber(item.item_count)} mặt hàng</td><td><span class="difference ${item.difference > 0 ? "positive" : item.difference < 0 ? "negative" : ""}">${item.difference > 0 ? "+" : ""}${formatNumber(item.difference)}</span></td><td><span class="badge ${item.status}">${statusLabel(item.status)}</span></td><td>${formatDateTime(item.created_at)}</td><td><div class="table-actions"><button class="action-button stocktake-view" data-id="${item.id}" type="button">Chi tiết</button>${item.status === "draft" && ["admin", "manager", "warehouse"].includes(role) ? `<button class="action-button stocktake-confirm" data-id="${item.id}" type="button">Xác nhận</button>` : ""}</div></td></tr>`).join("")
          : '<tr><td colspan="7"><div class="empty-state">Chưa có phiếu kiểm kê.</div></td></tr>';
        $$(".stocktake-view").forEach((button) => button.addEventListener("click", async () => {
          try {
            setLoading(true); const data = await api(`/api/stocktakes/${button.dataset.id}`);
            currentStocktake = data.item;
            $("#stocktake-detail").innerHTML = `<div class="document-head"><div><span class="brand-mark">DN</span><p>DNP LOGISTICS<br><small>Warehouse Management System</small></p></div><div><span class="eyebrow">PHIẾU KIỂM KÊ</span><h2>${escapeHtml(currentStocktake.code)}</h2></div></div>
              <div class="detail-grid"><div class="detail-field"><small>KHO</small><strong>${escapeHtml(currentStocktake.warehouse_name)}</strong></div><div class="detail-field"><small>TRẠNG THÁI</small><strong><span class="badge ${currentStocktake.status}">${statusLabel(currentStocktake.status)}</span></strong></div><div class="detail-field"><small>NGƯỜI LẬP</small><strong>${escapeHtml(currentStocktake.created_by_name || "—")}</strong></div><div class="detail-field"><small>GHI CHÚ</small><strong>${escapeHtml(currentStocktake.note || "—")}</strong></div></div>
              <div class="table-wrap"><table><thead><tr><th>Hàng hóa</th><th class="number">Tồn hệ thống</th><th class="number">Thực đếm</th><th class="number">Chênh lệch</th><th>Lý do</th></tr></thead><tbody>${(currentStocktake.items || []).map((line) => { const diff = line.counted_quantity - line.system_quantity; return `<tr><td><span class="sku">${escapeHtml(line.sku)}</span><span class="cell-title">${escapeHtml(line.name)}</span></td><td class="number">${formatNumber(line.system_quantity)}</td><td class="number">${formatNumber(line.counted_quantity)}</td><td class="number"><span class="difference ${diff > 0 ? "positive" : diff < 0 ? "negative" : ""}">${diff > 0 ? "+" : ""}${formatNumber(diff)}</span></td><td>${escapeHtml(line.reason || "Không chênh lệch")}</td></tr>`; }).join("")}</tbody></table></div>`;
            const draft = currentStocktake.status === "draft";
            $("#stocktake-detail-confirm").classList.toggle("hidden", !draft || !["admin", "manager", "warehouse"].includes(role));
            $("#stocktake-cancel").classList.toggle("hidden", !draft || !["admin", "manager", "warehouse"].includes(role));
            openModal($("#stocktake-detail-modal"));
          } catch (error) { toast(error.message, "error"); } finally { setLoading(false); }
        }));
        $$(".stocktake-confirm").forEach((button) => button.addEventListener("click", async () => {
          if (!await confirmAction("Xác nhận sẽ điều chỉnh tồn kho theo số thực đếm.", "Duyệt kiểm kê")) return;
          try { const result = await api(`/api/stocktakes/${button.dataset.id}/confirm`, { method: "POST" }); toast(result.message); await load(); }
          catch (error) { toast(error.message, "error"); }
        }));
      } catch (error) {
        $("#stocktake-body").innerHTML = `<tr><td colspan="7"><div class="empty-state error-state">${escapeHtml(error.message)} <button class="button secondary retry-stocktakes" type="button">Thử lại</button></div></td></tr>`;
        $(".retry-stocktakes")?.addEventListener("click", load);
      }
    };
    $("#stocktake-add")?.addEventListener("click", () => { form.reset(); clearErrors(form); $("#stocktake-line-body").innerHTML = ""; addLine(); openModal($("#stocktake-modal")); });
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (!form.reportValidity()) return;
      const payload = formData(form);
      payload.items = $$(".stocktake-line", form).map(controlsData);
      const button = $('button[type="submit"]', form); setButtonBusy(button, true);
      try { const result = await api("/api/stocktakes", { method: "POST", body: JSON.stringify(payload) }); closeModal($("#stocktake-modal")); toast(result.message); await load(); }
      catch (error) { showErrors(form, error.errors); toast(error.message, "error"); }
      finally { setButtonBusy(button, false); }
    });
    $("#stocktake-detail-confirm")?.addEventListener("click", async () => {
      if (!currentStocktake || !await confirmAction("Xác nhận sẽ điều chỉnh tồn kho theo số thực đếm.", "Duyệt kiểm kê")) return;
      try {
        const result = await api(`/api/stocktakes/${currentStocktake.id}/confirm`, { method: "POST" });
        closeModal($("#stocktake-detail-modal")); toast(result.message); await load();
      } catch (error) { toast(error.message, "error"); }
    });
    $("#receipt-start-picking")?.addEventListener("click", async () => {
      if (!await confirmAction(
        "Bắt đầu lấy hàng theo picking list? Phiếu sẽ chuyển sang trạng thái đang lấy hàng.",
        "Bắt đầu lấy hàng",
      )) return;
      try {
        setLoading(true);
        const result = await api(
          `${endpoint}/${$("#receipt-detail-modal").dataset.id}/start-picking`,
          { method: "POST" },
        );
        closeModal($("#receipt-detail-modal"));
        toast(result.message);
        await load();
      } catch (error) {
        toast(error.message, "error");
      } finally {
        setLoading(false);
      }
    });
    $("#receipt-reject")?.addEventListener("click", () => {
      const rejectForm = $("#receipt-reject-form");
      rejectForm?.reset();
      if (rejectForm) clearErrors(rejectForm);
      openModal($("#receipt-reject-modal"));
    });
    $("#receipt-reject-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const rejectForm = event.currentTarget;
      if (!rejectForm.reportValidity()) return;
      clearErrors(rejectForm);
      const button = $('button[type="submit"]', rejectForm);
      setButtonBusy(button, true, "Đang từ chối…");
      try {
        const result = await api(
          `${endpoint}/${$("#receipt-detail-modal").dataset.id}/reject`,
          { method: "POST", body: JSON.stringify(formData(rejectForm)) },
        );
        closeModal($("#receipt-reject-modal"));
        closeModal($("#receipt-detail-modal"));
        toast(result.message);
        await load();
      } catch (error) {
        showErrors(rejectForm, error.errors);
        toast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });
    $("#stocktake-cancel")?.addEventListener("click", async () => {
      if (!currentStocktake || !await confirmAction("Hủy phiếu kiểm kê nháp này?", "Hủy phiếu")) return;
      try {
        const result = await api(`/api/stocktakes/${currentStocktake.id}/cancel`, { method: "POST" });
        closeModal($("#stocktake-detail-modal")); toast(result.message); await load();
      } catch (error) { toast(error.message, "error"); }
    });
    $("#stocktake-print")?.addEventListener("click", () => printSection("stocktake"));
    let timer; $("#stocktake-search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    await load();
  }

  async function initReports() {
    const lookups = await getOperationLookups();
    $("#report-filters").elements.warehouse_id.innerHTML = optionList(lookups.warehouses, "Tất cả kho");
    $("#report-filters").elements.product_id.innerHTML = optionList(lookups.products, "Tất cả hàng hóa");
    $("#report-filters").elements.customer_id.innerHTML = optionList(lookups.customers, "Tất cả khách hàng");
    const load = async () => {
      const filters = formData($("#report-filters"));
      if (filters.from && filters.to && filters.from > filters.to) {
        toast("Ngày bắt đầu không được sau ngày kết thúc.", "error", "Khoảng thời gian chưa hợp lệ");
        $("#report-to").focus();
        return;
      }
      try {
        const data = await api(`/api/reports/summary?${new URLSearchParams(filters)}`);
        const cards = [
          ["blue", "Mặt hàng", data.summary.products], ["green", "Tổng tồn", data.summary.stock],
          ["amber", "Cảnh báo", data.summary.alerts], ["purple", "Phiếu hoàn tất", (data.receipt_counts.inbound || 0) + (data.receipt_counts.outbound || 0)],
        ];
        $("#report-stats").innerHTML = cards.map(([tone, label, value]) => `<article class="stat-card ${tone}"><small>${label}</small><strong>${formatNumber(value)}</strong><span class="cell-subtitle">Theo dữ liệu hiện tại</span></article>`).join("");
        const max = Math.max(...data.movement_totals.map((item) => item.quantity), 1);
        $("#movement-chart").innerHTML = data.movement_totals.length ? data.movement_totals.map((item) => `<div class="bar-row"><span class="bar-label">${escapeHtml(statusLabel(item.movement_type))}</span><span class="bar-track"><span class="bar-fill" style="width:${item.quantity / max * 100}%"></span></span><span class="bar-value">${formatNumber(item.quantity)}</span></div>`).join("") : '<div class="empty-state">Chưa có giao dịch hoàn tất.</div>';
        $("#stock-alerts").innerHTML = data.alerts.length ? data.alerts.map((item) => `<div class="alert-row"><span><b>${escapeHtml(item.sku)}</b><small>${escapeHtml(item.name)}</small></span><strong>${formatNumber(item.quantity)} / ${formatNumber(item.min_quantity)} ${escapeHtml(item.unit)}</strong></div>`).join("") : '<div class="empty-state compact">Không có cảnh báo.</div>';
        $("#movement-body").innerHTML = data.movements.length ? data.movements.map((item) => `<tr><td>${formatDateTime(item.created_at)}</td><td><span class="sku">${escapeHtml(item.reference_code)}</span></td><td>${escapeHtml(item.sku)} · ${escapeHtml(item.name)}</td><td><span class="badge info">${escapeHtml(item.movement_type)}</span></td><td class="number"><b>${item.quantity_change > 0 ? "+" : ""}${formatNumber(item.quantity_change)}</b></td><td class="number">${formatNumber(item.balance_after)}</td></tr>`).join("") : '<tr><td colspan="6"><div class="empty-state">Chưa có biến động tồn kho.</div></td></tr>';
      } catch (error) { toast(error.message, "error"); }
    };
    $("#report-filters").addEventListener("submit", (event) => { event.preventDefault(); load(); });
    $("#report-export")?.addEventListener("click", (event) => {
      event.preventDefault();
      const filters = formData($("#report-filters"));
      if (filters.from && filters.to && filters.from > filters.to) {
        toast("Ngày bắt đầu không được sau ngày kết thúc.", "error", "Khoảng thời gian chưa hợp lệ");
        return;
      }
      const query = new URLSearchParams(filters);
      [...query.entries()].forEach(([key, value]) => { if (!value) query.delete(key); });
      window.location.assign(`/api/reports/export.csv?${query}`);
    });
    $("#report-print").addEventListener("click", () => printSection("report"));
    await load();
  }

  async function initSettings() {
    const roleBody = $("#role-body");
    const unitBody = $("#unit-body");
    const roleForm = $("#role-form");
    const unitForm = $("#unit-form");
    let roles = [];
    let units = [];

    const recordStatus = (item) => item.status || "active";
    const statusBadge = (item) => {
      const status = recordStatus(item);
      return `<span class="badge ${status}">${status === "active" ? "Hoạt động" : "Ngừng hoạt động"}</span>`;
    };
    const tableMessage = (message, colspan, error = false, retryClass = "") =>
      `<tr><td colspan="${colspan}"><div class="empty-state ${error ? "error-state" : ""}" ${error ? 'role="alert"' : ""}>${escapeHtml(message)}${retryClass ? ` <button class="button secondary small ${retryClass}" type="button">Thử lại</button>` : ""}</div></td></tr>`;

    function updateSummary(selector, items, label) {
      const active = items.filter((item) => recordStatus(item) === "active").length;
      $(selector).textContent = `${items.length} ${label} · ${active} đang hoạt động`;
    }

    function bindRoleActions() {
      $$(".settings-edit-role", roleBody).forEach((button) => button.addEventListener("click", () => {
        const item = roles.find((entry) => entry.id === Number(button.dataset.id));
        if (!item) return;
        roleForm.reset(); clearErrors(roleForm);
        roleForm.elements.id.value = item.id;
        roleForm.elements.code.value = item.code || "";
        roleForm.elements.name.value = item.name || "";
        roleForm.elements.description.value = item.description || "";
        roleForm.elements.status.value = recordStatus(item);
        $("#role-modal-title").textContent = "Cập nhật vai trò";
        openModal($("#role-modal"));
      }));
      $$(".settings-toggle-role", roleBody).forEach((button) => button.addEventListener("click", async () => {
        const item = roles.find((entry) => entry.id === Number(button.dataset.id));
        if (!item) return;
        const next = recordStatus(item) === "active" ? "inactive" : "active";
        if (!await confirmAction(
          `${next === "inactive" ? "Ngừng" : "Kích hoạt"} vai trò “${item.name}”?`,
          next === "inactive" ? "Ngừng hoạt động" : "Kích hoạt",
        )) return;
        button.disabled = true;
        try {
          const result = await api(`/api/roles/${item.id}`, {
            method: "PUT",
            body: JSON.stringify({
              code: item.code,
              name: item.name,
              description: item.description || "",
              status: next,
            }),
          });
          toast(result.message || "Đã cập nhật vai trò.");
          await loadRoles();
        } catch (error) {
          toast(error.message, "error");
        } finally {
          button.disabled = false;
        }
      }));
    }

    function bindUnitActions() {
      $$(".settings-edit-unit", unitBody).forEach((button) => button.addEventListener("click", () => {
        const item = units.find((entry) => entry.id === Number(button.dataset.id));
        if (!item) return;
        unitForm.reset(); clearErrors(unitForm);
        unitForm.elements.id.value = item.id;
        unitForm.elements.code.value = item.code || "";
        unitForm.elements.name.value = item.name || "";
        unitForm.elements.status.value = recordStatus(item);
        unitForm.elements.allow_break_pack.checked = Boolean(item.allow_break_pack);
        $("#unit-modal-title").textContent = "Cập nhật đơn vị tính";
        openModal($("#unit-modal"));
      }));
      $$(".settings-toggle-unit", unitBody).forEach((button) => button.addEventListener("click", async () => {
        const item = units.find((entry) => entry.id === Number(button.dataset.id));
        if (!item) return;
        const next = recordStatus(item) === "active" ? "inactive" : "active";
        if (!await confirmAction(
          `${next === "inactive" ? "Ngừng" : "Kích hoạt"} đơn vị “${item.name}”?`,
          next === "inactive" ? "Ngừng hoạt động" : "Kích hoạt",
        )) return;
        button.disabled = true;
        try {
          const result = await api(`/api/units/${item.id}`, {
            method: "PUT",
            body: JSON.stringify({
              code: item.code,
              name: item.name,
              allow_break_pack: Boolean(item.allow_break_pack),
              status: next,
            }),
          });
          toast(result.message || "Đã cập nhật đơn vị tính.");
          await loadUnits();
        } catch (error) {
          toast(error.message, "error");
        } finally {
          button.disabled = false;
        }
      }));
    }

    async function loadRoles() {
      roleBody.setAttribute("aria-busy", "true");
      roleBody.innerHTML = tableMessage("Đang tải vai trò…", 5);
      try {
        const data = await api("/api/roles");
        roles = data.items || [];
        roleBody.innerHTML = roles.length ? roles.map((item) => `
          <tr>
            <td><span class="sku">${escapeHtml(item.code)}</span></td>
            <td><span class="cell-title">${escapeHtml(item.name)}</span></td>
            <td class="settings-description">${escapeHtml(item.description || "Chưa có mô tả")}</td>
            <td>${statusBadge(item)}</td>
            <td><div class="table-actions">
              <button class="action-button settings-edit-role" data-id="${item.id}" type="button" aria-label="Sửa vai trò ${escapeHtml(item.name)}">Sửa</button>
              <button class="action-button ${recordStatus(item) === "active" ? "danger" : ""} settings-toggle-role" data-id="${item.id}" type="button">${recordStatus(item) === "active" ? "Ngừng" : "Kích hoạt"}</button>
            </div></td>
          </tr>`).join("") : tableMessage("Chưa có vai trò nào.", 5);
        updateSummary("#role-summary", roles, "vai trò");
        bindRoleActions();
      } catch (error) {
        roleBody.innerHTML = tableMessage(error.message, 5, true, "retry-roles");
        $(".retry-roles", roleBody)?.addEventListener("click", loadRoles);
        $("#role-summary").textContent = "Không thể tải dữ liệu vai trò";
      } finally {
        roleBody.removeAttribute("aria-busy");
      }
    }

    async function loadUnits() {
      unitBody.setAttribute("aria-busy", "true");
      unitBody.innerHTML = tableMessage("Đang tải đơn vị tính…", 5);
      try {
        const data = await api("/api/units");
        units = data.items || [];
        unitBody.innerHTML = units.length ? units.map((item) => `
          <tr>
            <td><span class="sku">${escapeHtml(item.code)}</span></td>
            <td><span class="cell-title">${escapeHtml(item.name)}</span></td>
            <td><span class="badge ${item.allow_break_pack ? "info" : "neutral"}">${item.allow_break_pack ? "Cho phép" : "Nguyên kiện"}</span></td>
            <td>${statusBadge(item)}</td>
            <td><div class="table-actions">
              <button class="action-button settings-edit-unit" data-id="${item.id}" type="button" aria-label="Sửa đơn vị ${escapeHtml(item.name)}">Sửa</button>
              <button class="action-button ${recordStatus(item) === "active" ? "danger" : ""} settings-toggle-unit" data-id="${item.id}" type="button">${recordStatus(item) === "active" ? "Ngừng" : "Kích hoạt"}</button>
            </div></td>
          </tr>`).join("") : tableMessage("Chưa có đơn vị tính nào.", 5);
        updateSummary("#unit-summary", units, "đơn vị");
        bindUnitActions();
      } catch (error) {
        unitBody.innerHTML = tableMessage(error.message, 5, true, "retry-units");
        $(".retry-units", unitBody)?.addEventListener("click", loadUnits);
        $("#unit-summary").textContent = "Không thể tải dữ liệu đơn vị tính";
      } finally {
        unitBody.removeAttribute("aria-busy");
      }
    }

    $("#role-add")?.addEventListener("click", () => {
      roleForm.reset(); clearErrors(roleForm);
      roleForm.elements.id.value = "";
      roleForm.elements.status.value = "active";
      $("#role-modal-title").textContent = "Thêm vai trò";
      openModal($("#role-modal"));
    });
    $("#unit-add")?.addEventListener("click", () => {
      unitForm.reset(); clearErrors(unitForm);
      unitForm.elements.id.value = "";
      unitForm.elements.status.value = "active";
      $("#unit-modal-title").textContent = "Thêm đơn vị tính";
      openModal($("#unit-modal"));
    });

    roleForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!roleForm.reportValidity()) return;
      clearErrors(roleForm);
      const payload = formData(roleForm);
      const id = payload.id; delete payload.id;
      payload.code = payload.code.trim().toLowerCase();
      payload.name = payload.name.trim();
      payload.description = payload.description.trim();
      const button = $('button[type="submit"]', roleForm);
      setButtonBusy(button, true);
      try {
        const result = await api(id ? `/api/roles/${id}` : "/api/roles", {
          method: id ? "PUT" : "POST",
          body: JSON.stringify(payload),
        });
        closeModal($("#role-modal"));
        toast(result.message || "Đã lưu vai trò.");
        await loadRoles();
      } catch (error) {
        showErrors(roleForm, error.errors);
        toast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });

    unitForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!unitForm.reportValidity()) return;
      clearErrors(unitForm);
      const payload = formData(unitForm);
      const id = payload.id; delete payload.id;
      payload.code = payload.code.trim().toUpperCase();
      payload.name = payload.name.trim();
      payload.allow_break_pack = unitForm.elements.allow_break_pack.checked;
      const button = $('button[type="submit"]', unitForm);
      setButtonBusy(button, true);
      try {
        const result = await api(id ? `/api/units/${id}` : "/api/units", {
          method: id ? "PUT" : "POST",
          body: JSON.stringify(payload),
        });
        closeModal($("#unit-modal"));
        toast(result.message || "Đã lưu đơn vị tính.");
        await loadUnits();
      } catch (error) {
        showErrors(unitForm, error.errors);
        toast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });

    await Promise.all([loadRoles(), loadUnits()]);
  }

  function initNetworkStatus() {
    const banner = $("#offline-banner");
    const update = () => banner?.classList.toggle("hidden", navigator.onLine);
    window.addEventListener("online", update); window.addEventListener("offline", update); update();
  }

  async function boot() {
    initModals();
    if (page === "login") { initLogin(); return; }
    try {
      await initSession();
      initChrome();
      initNetworkStatus();
      if (page === "dashboard") await initDashboard();
      if (page === "inventory") await initInventory();
      if (page === "categories") await initCategories();
      if (page === "products") await initProducts();
      if (page === "customers" || page === "suppliers") await initPartners();
      if (page === "warehouses") await initWarehouses();
      if (page === "inbound" || page === "outbound") await initReceipts();
      if (page === "stocktakes") await initStocktakes();
      if (page === "reports") await initReports();
      if (page === "users") await initUsers();
      if (page === "settings") await initSettings();
      if (page === "profile") initProfile();
      if (page === "audit") await loadAudit();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  boot();
})();
