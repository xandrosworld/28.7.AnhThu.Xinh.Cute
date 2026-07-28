(() => {
  "use strict";

  const state = {
    csrfToken: "",
    user: null,
    inventory: [],
    categories: [],
    users: [],
  };

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
    const data = await response.json().catch(() => ({ ok: false, message: "Phản hồi máy chủ không hợp lệ." }));
    if (!response.ok) {
      if (response.status === 401 && page !== "login") window.location.href = "/";
      const error = new Error(data.message || "Yêu cầu không thành công.");
      error.status = response.status;
      error.errors = data.errors || {};
      throw error;
    }
    return data;
  }

  function formData(form) {
    return Object.fromEntries(new FormData(form).entries());
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
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    const focusable = $("input:not([type=hidden]), select, textarea, button", modal);
    window.setTimeout(() => focusable?.focus(), 30);
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  function initModals() {
    $$(".modal-backdrop").forEach((modal) => {
      $$(".modal-close", modal).forEach((button) => button.addEventListener("click", () => closeModal(modal)));
      modal.addEventListener("mousedown", (event) => {
        if (event.target === modal) closeModal(modal);
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") $$(".modal-backdrop:not(.hidden)").forEach(closeModal);
    });
  }

  function pagination(container, data, callback) {
    if (!container) return;
    const { page: current, pages } = data;
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
          <td class="number"><b>${formatNumber(item.quantity)}</b> ${escapeHtml(item.unit)}</td>
          <td>${statusBadge(item)}</td>
          <td>${formatDateTime(item.updated_at)}</td>
          <td><div class="table-actions"><button class="action-button detail-button" data-id="${item.id}" type="button">Chi tiết</button>${["admin", "manager"].includes(role) ? `<button class="action-button adjust-button" data-id="${item.id}" type="button">Kiểm kê</button>` : ""}</div></td>
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
      $("#inventory-detail").innerHTML = `
        <div class="detail-hero"><div><span class="sku">${escapeHtml(item.sku)}</span><h3>${escapeHtml(item.name)}</h3></div><div class="detail-quantity"><strong>${formatNumber(item.quantity)}</strong><small>${escapeHtml(item.unit)} hiện có</small></div></div>
        <div class="detail-grid">
          <div class="detail-field"><small>DANH MỤC</small><strong>${escapeHtml(item.category_name)}</strong></div>
          <div class="detail-field"><small>KHO</small><strong>${escapeHtml(item.warehouse_name)}</strong></div>
          <div class="detail-field"><small>VỊ TRÍ</small><strong>${escapeHtml(item.location || "—")}</strong></div>
          <div class="detail-field"><small>NGƯỠNG TỐI THIỂU</small><strong>${formatNumber(item.min_quantity)} ${escapeHtml(item.unit)}</strong></div>
          <div class="detail-field"><small>TRẠNG THÁI</small><strong>${statusBadge(item)}</strong></div>
          <div class="detail-field"><small>CẬP NHẬT</small><strong>${formatDateTime(item.updated_at)}</strong></div>
        </div>
        <h3 class="history-title">Lịch sử điều chỉnh gần nhất</h3>
        <div>${data.adjustments.length ? data.adjustments.map((entry) => `
          <div class="history-row"><div><b>${escapeHtml(entry.reason)}</b><br><span>${escapeHtml(entry.created_by_name)} · ${formatDateTime(entry.created_at)}${entry.note ? ` · ${escapeHtml(entry.note)}` : ""}</span></div><div class="difference ${entry.difference > 0 ? "positive" : "negative"}">${entry.difference > 0 ? "+" : ""}${formatNumber(entry.difference)}</div></div>`).join("") : '<div class="empty-state">Chưa có lịch sử điều chỉnh.</div>'}</div>`;
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
    const data = await api(`/api/categories?search=${encodeURIComponent(search)}`);
    state.categories = data.items;
    $("#category-count").textContent = `${data.items.length} danh mục`;
    const canEdit = ["admin", "manager"].includes(role);
    $("#category-body").innerHTML = data.items.length ? data.items.map((item) => `
      <tr><td><span class="sku">${escapeHtml(item.code)}</span></td><td><span class="cell-title">${escapeHtml(item.name)}</span></td><td>${escapeHtml(item.description || "—")}</td><td class="number">${formatNumber(item.product_count)}</td><td><span class="badge ${item.status}">${item.status === "active" ? "Hoạt động" : "Ngừng hoạt động"}</span></td><td><div class="table-actions">${canEdit ? `<button class="action-button category-edit" data-id="${item.id}" type="button">Sửa</button>` : ""}${role === "admin" ? `<button class="action-button danger category-delete" data-id="${item.id}" type="button">Xóa</button>` : ""}</div></td></tr>`).join("")
      : '<tr><td colspan="6"><div class="empty-state">Không có danh mục phù hợp.</div></td></tr>';
    $$(".category-edit").forEach((button) => button.addEventListener("click", () => editCategory(button.dataset.id)));
    $$(".category-delete").forEach((button) => button.addEventListener("click", () => deleteCategory(button.dataset.id)));
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
    if (!window.confirm(`Xóa danh mục “${item.name}”? Thao tác này không thể hoàn tác.`)) return;
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
    const data = await api(`/api/users?search=${encodeURIComponent(search)}`);
    state.users = data.items;
    $("#user-count").textContent = `${data.items.length} người dùng`;
    $("#user-body").innerHTML = data.items.length ? data.items.map((item) => `
      <tr><td><div style="display:flex;align-items:center;gap:9px"><span class="avatar">${escapeHtml(item.avatar_initials)}</span><div><span class="cell-title">${escapeHtml(item.full_name)}</span><span class="cell-subtitle">@${escapeHtml(item.username)}</span></div></div></td>
      <td><span class="cell-title">${escapeHtml(item.email)}</span><span class="cell-subtitle">${escapeHtml(item.phone || "Chưa cập nhật")}</span></td>
      <td><span class="badge info">${escapeHtml(item.role_label)}</span></td><td><span class="badge ${item.status}">${item.status === "active" ? "Hoạt động" : "Đã khóa"}</span></td>
      <td>${formatDateTime(item.created_at)}</td><td><div class="table-actions"><button class="action-button user-edit" data-id="${item.id}" type="button">Sửa</button><button class="action-button danger user-delete" data-id="${item.id}" type="button">Xóa</button></div></td></tr>`).join("")
      : '<tr><td colspan="6"><div class="empty-state">Không có người dùng phù hợp.</div></td></tr>';
    $$(".user-edit").forEach((button) => button.addEventListener("click", () => editUser(button.dataset.id)));
    $$(".user-delete").forEach((button) => button.addEventListener("click", () => deleteUser(button.dataset.id)));
  }

  function editUser(id) {
    const item = state.users.find((entry) => entry.id === Number(id));
    const form = $("#user-form"); form.reset(); clearErrors(form);
    Object.entries(item).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ""; });
    form.elements.password.value = ""; $("#password-required").classList.add("hidden");
    $("#user-modal-title").textContent = "Cập nhật người dùng"; openModal($("#user-modal"));
  }

  async function deleteUser(id) {
    const item = state.users.find((entry) => entry.id === Number(id));
    if (!window.confirm(`Xóa tài khoản “${item.username}”? Nếu tài khoản có lịch sử nghiệp vụ, hệ thống sẽ từ chối xóa.`)) return;
    try { const result = await api(`/api/users/${id}`, { method: "DELETE" }); toast(result.message); await loadUsers(); }
    catch (error) { toast(error.message, "error"); }
  }

  async function initUsers() {
    let debounce;
    $("#user-search").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(loadUsers, 250); });
    $("#user-add").addEventListener("click", () => {
      const form = $("#user-form"); form.reset(); clearErrors(form); form.elements.id.value = "";
      $("#password-required").classList.remove("hidden"); $("#user-modal-title").textContent = "Thêm người dùng"; openModal($("#user-modal"));
    });
    $("#user-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget; clearErrors(form);
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

  async function boot() {
    initModals();
    if (page === "login") { initLogin(); return; }
    try {
      await initSession();
      initChrome();
      if (page === "dashboard") await initDashboard();
      if (page === "inventory") await initInventory();
      if (page === "categories") await initCategories();
      if (page === "users") await initUsers();
      if (page === "profile") initProfile();
      if (page === "audit") await loadAudit();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  boot();
})();
