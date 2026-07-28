"use strict";

const body = document.body;
const page = body.dataset.page;
const entityId = Number(body.dataset.id || 0);
let csrfToken = "";
const money = new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("vi-VN");
const statusText = { pending: "Chờ duyệt", processing: "Đang xử lý", completed: "Hoàn thành", cancelled: "Đã hủy" };
const stockText = { in_stock: "Còn hàng", low_stock: "Sắp hết", out_of_stock: "Hết hàng" };

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]
));

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (csrfToken && options.method && !["GET", "HEAD"].includes(options.method)) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const config = { ...options, headers, credentials: "same-origin" };
  const response = await fetch(path, config);
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    if (response.status === 401 && page !== "login") location.assign("/login");
    if (data.error && typeof data.error === "object") data.error = data.error.message;
    const error = new Error(data.error || "Không thể xử lý yêu cầu.");
    error.details = data.details;
    throw error;
  }
  return data;
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  document.querySelector("#toast-region").append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function handleError(error) {
  console.error(error);
  toast(error.message || "Đã có lỗi xảy ra.", "error");
}

function setLoading(element, active) {
  element?.classList.toggle("loading", active);
  element?.querySelectorAll("button").forEach(button => button.disabled = active);
}

function confirmAction(message, title = "Xác nhận thao tác") {
  const dialog = document.querySelector("#confirm-dialog");
  dialog.querySelector("#confirm-title").textContent = title;
  dialog.querySelector("#confirm-message").textContent = message;
  dialog.showModal();
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

function isoDate(value) {
  if (!value) return "—";
  const date = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("vi-VN").format(date);
}

function dateTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function fieldData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function validateForm(form) {
  if (!form.checkValidity()) {
    form.reportValidity();
    toast("Vui lòng kiểm tra các trường bắt buộc.", "error");
    return false;
  }
  return true;
}

async function loadCategories(select) {
  const categories = await api("/api/categories");
  if (select) {
    const current = select.value;
    const first = select.querySelector("option")?.outerHTML || "";
    select.innerHTML = first + categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
    select.value = current;
  }
  return categories;
}

function renderPagination(container, pagination, callback) {
  if (!container) return;
  const { page: current, pages, total } = pagination;
  const range = [...new Set([1, current - 1, current, current + 1, pages])].filter(p => p > 0 && p <= pages).sort((a, b) => a - b);
  container.innerHTML = `<span>${number.format(total)} kết quả · Trang ${current}/${pages}</span><div class="page-buttons">
    <button type="button" data-page="${current - 1}" ${current === 1 ? "disabled" : ""} aria-label="Trang trước">‹</button>
    ${range.map((p, i) => `${i && p - range[i - 1] > 1 ? "<span>…</span>" : ""}<button type="button" data-page="${p}" class="${p === current ? "active" : ""}">${p}</button>`).join("")}
    <button type="button" data-page="${current + 1}" ${current === pages ? "disabled" : ""} aria-label="Trang sau">›</button>
  </div>`;
  container.querySelectorAll("button[data-page]").forEach(button => button.addEventListener("click", () => callback(Number(button.dataset.page))));
}

function productStatus(product) {
  return `<span class="status ${product.status}">${stockText[product.status]}</span>`;
}

async function initProducts() {
  const form = document.querySelector("#product-filters");
  const rows = document.querySelector("#product-rows");
  const initialParams = new URLSearchParams(location.search);
  if (initialParams.get("q")) form.elements.q.value = initialParams.get("q");
  const categories = await loadCategories(document.querySelector("#category-filter"));
  async function load(pageNumber = 1) {
    try {
      setLoading(rows.closest(".panel"), true);
      const params = new URLSearchParams(fieldData(form));
      params.set("page", pageNumber);
      const [result, stats] = await Promise.all([api(`/api/products?${params}`), api("/api/products/stats")]);
      document.querySelectorAll("[data-stat]").forEach(node => node.textContent = number.format(stats[node.dataset.stat] || 0));
      rows.innerHTML = result.items.length ? result.items.map(product => {
        const percent = Math.min(100, Math.round(product.quantity / product.max_stock * 100));
        return `<tr>
          <td><div class="sku">${escapeHtml(product.sku)}</div><div class="subtext">${escapeHtml(product.barcode)}</div></td>
          <td class="product-cell"><b>${escapeHtml(product.name)}</b><span class="subtext">${escapeHtml(product.unit)}</span></td>
          <td><span class="tag">${escapeHtml(product.category_name)}</span></td>
          <td>${escapeHtml(product.location)}</td>
          <td><b>${number.format(product.quantity)}</b> / ${number.format(product.max_stock)}<div class="stock-meter"><i style="width:${percent}%"></i></div></td>
          <td class="number">${money.format(product.unit_price)}</td><td>${productStatus(product)}</td>
          <td><div class="actions"><a class="icon-btn" href="/hang-hoa/${product.id}" title="Xem chi tiết" aria-label="Xem ${escapeHtml(product.name)}">◉</a><a class="icon-btn" href="/hang-hoa/${product.id}/sua" title="Chỉnh sửa" aria-label="Sửa ${escapeHtml(product.name)}">✎</a><button class="icon-btn delete" data-delete="${product.id}" data-name="${escapeHtml(product.name)}" title="Xóa" aria-label="Xóa ${escapeHtml(product.name)}">×</button></div></td>
        </tr>`;
      }).join("") : '<tr><td colspan="8" class="empty">Không tìm thấy hàng hóa phù hợp.</td></tr>';
      renderPagination(document.querySelector("#pagination"), result.pagination, load);
      rows.querySelectorAll("[data-delete]").forEach(button => button.addEventListener("click", async () => {
        if (!await confirmAction(`Xóa “${button.dataset.name}”? Thao tác này không thể hoàn tác.`)) return;
        try { await api(`/api/products/${button.dataset.delete}`, { method: "DELETE" }); toast("Đã xóa hàng hóa."); await load(result.pagination.page); }
        catch (error) { handleError(error); }
      }));
    } catch (error) { rows.innerHTML = `<tr><td colspan="8" class="empty">Không tải được dữ liệu.</td></tr>`; handleError(error); }
    finally { setLoading(rows.closest(".panel"), false); }
  }
  form.addEventListener("submit", event => { event.preventDefault(); load(); });
  form.addEventListener("reset", () => window.setTimeout(() => load(), 0));
  initCategoryManager(categories, load);
  await load();
}

function initCategoryManager(initialCategories, reloadProducts) {
  const list = document.querySelector("#category-list");
  const form = document.querySelector("#category-form");
  if (!list || !form) return;
  let categories = initialCategories;
  const render = () => {
    list.innerHTML = categories.map(category => `<tr><td><b>${escapeHtml(category.code)}</b></td><td>${escapeHtml(category.name)}</td><td>${number.format(category.product_count)} hàng hóa</td><td><div class="actions"><button class="icon-btn" data-edit-category="${category.id}" title="Sửa">✎</button><button class="icon-btn delete" data-delete-category="${category.id}" title="Xóa">×</button></div></td></tr>`).join("");
    list.querySelectorAll("[data-edit-category]").forEach(button => button.onclick = () => {
      const item = categories.find(c => c.id === Number(button.dataset.editCategory));
      form.elements.id.value = item.id; form.elements.code.value = item.code; form.elements.name.value = item.name; form.elements.description.value = item.description || "";
      form.querySelector("button[type=submit]").textContent = "Lưu danh mục";
      form.elements.code.focus();
    });
    list.querySelectorAll("[data-delete-category]").forEach(button => button.onclick = async () => {
      if (!await confirmAction("Xóa danh mục này?")) return;
      try { await api(`/api/categories/${button.dataset.deleteCategory}`, { method: "DELETE" }); toast("Đã xóa danh mục."); await refresh(); }
      catch (error) { handleError(error); }
    });
  };
  const refresh = async () => {
    categories = await loadCategories(document.querySelector("#category-filter"));
    render(); await reloadProducts();
  };
  form.onsubmit = async event => {
    event.preventDefault(); if (!validateForm(form)) return;
    const data = fieldData(form); const id = data.id; delete data.id;
    try {
      await api(id ? `/api/categories/${id}` : "/api/categories", { method: id ? "PATCH" : "POST", body: JSON.stringify(data) });
      toast(id ? "Đã cập nhật danh mục." : "Đã thêm danh mục."); form.reset(); form.elements.id.value = ""; form.querySelector("button[type=submit]").textContent = "Thêm danh mục"; await refresh();
    } catch (error) { handleError(error); }
  };
  form.querySelector("[data-cancel-category]").onclick = () => { form.reset(); form.elements.id.value = ""; form.querySelector("button[type=submit]").textContent = "Thêm danh mục"; };
  render();
}

async function initProductForm() {
  const form = document.querySelector("#product-form");
  const select = document.querySelector("#category-select");
  await loadCategories(select);
  if (page === "product-edit") {
    try {
      const product = await api(`/api/products/${entityId}`);
      Object.entries(product).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
    } catch (error) { handleError(error); }
  } else {
    form.elements.quantity.value = 0;
    form.elements.min_stock.value = 10;
    form.elements.max_stock.value = 100;
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (!validateForm(form)) return;
    const data = fieldData(form);
    ["category_id", "quantity", "min_stock", "max_stock", "unit_price"].forEach(key => data[key] = Number(data[key]));
    if (data.max_stock < data.min_stock || data.quantity > data.max_stock) { toast("Tồn tối đa phải ≥ tồn tối thiểu và số lượng hiện tại.", "error"); return; }
    try {
      setLoading(form, true);
      const result = await api(page === "product-edit" ? `/api/products/${entityId}` : "/api/products", { method: page === "product-edit" ? "PUT" : "POST", body: JSON.stringify(data) });
      toast(result.message);
      window.setTimeout(() => location.assign(page === "product-edit" ? `/hang-hoa/${entityId}` : `/hang-hoa/${result.id}`), 450);
    } catch (error) { handleError(error); setLoading(form, false); }
  });
}

async function initProductDetail() {
  const target = document.querySelector("#product-detail");
  try {
    const p = await api(`/api/products/${entityId}`);
    target.innerHTML = `<div class="detail-hero"><div><span class="tag">${escapeHtml(p.category_name)}</span><h1>${escapeHtml(p.name)}</h1><p>${escapeHtml(p.sku)} · ${escapeHtml(p.barcode)}</p></div><div class="detail-actions"><a class="btn" href="/hang-hoa">Quay lại</a><a class="btn primary" href="/hang-hoa/${p.id}/sua">✎ Cập nhật</a></div></div>
    <div class="detail-grid"><section class="panel"><h2>Thông tin hàng hóa</h2><dl class="info-list"><div><dt>Đơn vị tính</dt><dd>${escapeHtml(p.unit)}</dd></div><div><dt>Vị trí</dt><dd>${escapeHtml(p.location)}</dd></div><div><dt>Đơn giá</dt><dd>${money.format(p.unit_price)}</dd></div><div><dt>Số phiếu đã dùng</dt><dd>${number.format(p.outbound_count)}</dd></div><div class="wide"><dt>Mô tả</dt><dd>${escapeHtml(p.description || "Chưa có mô tả")}</dd></div></dl></section>
    <section class="panel"><h2>Tình trạng tồn kho</h2><dl class="info-list"><div><dt>Trạng thái</dt><dd>${productStatus(p)}</dd></div><div><dt>Tồn hiện tại</dt><dd>${number.format(p.quantity)} ${escapeHtml(p.unit)}</dd></div><div><dt>Tồn tối thiểu</dt><dd>${number.format(p.min_stock)}</dd></div><div><dt>Tồn tối đa</dt><dd>${number.format(p.max_stock)}</dd></div><div class="wide"><dt>Giá trị tồn</dt><dd>${money.format(p.inventory_value)}</dd></div></dl></section></div>`;
  } catch (error) { target.innerHTML = '<div class="panel empty">Không tìm thấy hàng hóa.</div>'; handleError(error); }
}

function orderStatus(order) { return `<span class="status ${order.status}">${statusText[order.status]}</span>`; }

async function initOrders() {
  const form = document.querySelector("#order-filters");
  const rows = document.querySelector("#order-rows");
  const initialParams = new URLSearchParams(location.search);
  if (initialParams.get("q")) form.elements.q.value = initialParams.get("q");
  async function load(pageNumber = 1) {
    try {
      setLoading(rows.closest(".panel"), true);
      const params = new URLSearchParams(fieldData(form)); params.set("page", pageNumber);
      const [result, stats] = await Promise.all([api(`/api/outbound-orders?${params}`), api("/api/outbound-orders/stats")]);
      document.querySelectorAll("[data-stat]").forEach(node => node.textContent = number.format(stats[node.dataset.stat] || 0));
      rows.innerHTML = result.items.length ? result.items.map(order => `<tr>
        <td><a class="sku" href="/xuat-kho/${order.id}">${escapeHtml(order.code)}</a></td><td>${isoDate(order.outbound_date)}</td><td class="product-cell"><b>${escapeHtml(order.customer_name)}</b><span class="subtext">${escapeHtml(order.vehicle_no || "Chưa có biển số")}</span></td><td>${number.format(order.line_count)}</td><td>${number.format(order.total_quantity)}</td><td class="number">${money.format(order.total_value)}</td><td>${orderStatus(order)}</td>
        <td><div class="actions"><a class="icon-btn" href="/xuat-kho/${order.id}" title="Xem chi tiết chỉ đọc">◉</a></div></td></tr>`).join("") : '<tr><td colspan="8" class="empty">Không tìm thấy phiếu xuất phù hợp.</td></tr>';
      renderPagination(document.querySelector("#pagination"), result.pagination, load);
    } catch (error) { handleError(error); } finally { setLoading(rows.closest(".panel"), false); }
  }
  form.onsubmit = event => { event.preventDefault(); load(); };
  form.onreset = () => setTimeout(() => load(), 0);
  load();
}

async function initOrderForm() {
  const form = document.querySelector("#order-form");
  const lines = document.querySelector("#order-lines");
  const products = (await api("/api/products?per_page=50")).items;
  let initialOrder = null;
  const options = (selected) => `<option value="">Chọn hàng hóa</option>${products.map(p => `<option value="${p.id}" data-stock="${p.quantity}" data-price="${p.unit_price}" data-unit="${escapeHtml(p.unit)}" ${p.id === selected ? "selected" : ""}>${escapeHtml(p.sku)} · ${escapeHtml(p.name)}</option>`).join("")}`;
  function addLine(item = {}) {
    const row = document.createElement("tr");
    row.innerHTML = `<td><select class="line-product" required aria-label="Sản phẩm">${options(item.product_id)}</select></td><td class="line-stock">—</td><td><input class="line-quantity" type="number" min="1" step="1" required value="${item.quantity || 1}" aria-label="Số lượng xuất"></td><td class="line-price number">—</td><td class="line-total number">—</td><td><button type="button" class="remove-line" title="Xóa dòng" aria-label="Xóa dòng">×</button></td>`;
    lines.append(row);
    row.querySelector(".line-product").onchange = updateSummary;
    row.querySelector(".line-quantity").oninput = updateSummary;
    row.querySelector(".remove-line").onclick = () => { row.remove(); updateSummary(); };
    updateSummary();
  }
  function updateSummary() {
    let qty = 0, total = 0;
    lines.querySelectorAll("tr").forEach(row => {
      const select = row.querySelector(".line-product"), option = select.selectedOptions[0], amount = Number(row.querySelector(".line-quantity").value || 0), price = Number(option?.dataset.price || 0), stock = Number(option?.dataset.stock || 0);
      row.querySelector(".line-stock").textContent = option?.value ? `${number.format(stock)} ${option.dataset.unit}` : "—";
      row.querySelector(".line-price").textContent = option?.value ? money.format(price) : "—";
      row.querySelector(".line-total").textContent = option?.value ? money.format(price * amount) : "—";
      row.querySelector(".line-quantity").classList.toggle("invalid", amount > stock);
      if (option?.value) { qty += amount; total += price * amount; }
    });
    document.querySelector("#sum-lines").textContent = lines.querySelectorAll("tr").length;
    document.querySelector("#sum-quantity").textContent = number.format(qty);
    document.querySelector("#sum-value").textContent = money.format(total);
  }
  window.updateSummary = updateSummary;
  document.querySelector("#add-line").onclick = () => addLine();
  form.elements.outbound_date.value = new Date().toISOString().slice(0, 10);
  if (page === "order-edit") {
    try {
      initialOrder = await api(`/api/outbound-orders/${entityId}`);
      Object.entries(initialOrder).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
      initialOrder.items.forEach(addLine);
    } catch (error) { handleError(error); }
  } else addLine();
  form.onsubmit = async event => {
    event.preventDefault(); if (!validateForm(form)) return;
    const selected = new Set(), items = [];
    let invalid = "";
    lines.querySelectorAll("tr").forEach(row => {
      const select = row.querySelector(".line-product"), productId = Number(select.value), quantity = Number(row.querySelector(".line-quantity").value), stock = Number(select.selectedOptions[0]?.dataset.stock || 0);
      if (!productId) invalid = "Vui lòng chọn sản phẩm cho mọi dòng.";
      else if (selected.has(productId)) invalid = "Một sản phẩm không được lặp lại trong phiếu.";
      else if (quantity < 1) invalid = "Số lượng xuất phải lớn hơn 0.";
      else if (quantity > stock) invalid = `Số lượng xuất vượt tồn khả dụng của ${select.selectedOptions[0].text}.`;
      selected.add(productId); items.push({ product_id: productId, quantity });
    });
    if (!items.length) invalid = "Phiếu xuất phải có ít nhất một mặt hàng.";
    if (invalid) { toast(invalid, "error"); return; }
    const data = { ...fieldData(form), items };
    try {
      setLoading(form, true);
      const result = await api(page === "order-edit" ? `/api/outbound-orders/${entityId}` : "/api/outbound-orders", { method: page === "order-edit" ? "PUT" : "POST", body: JSON.stringify(data) });
      toast(result.message); setTimeout(() => location.assign(`/xuat-kho/${entityId || result.id}`), 450);
    } catch (error) { handleError(error); setLoading(form, false); }
  };
}

function orderItemsTable(order, inspection = false) {
  return `<div class="table-scroll"><table><thead><tr><th>SKU</th><th>SẢN PHẨM</th><th>ĐVT</th><th>TỒN HIỆN TẠI</th><th>SL XUẤT</th><th>ĐƠN GIÁ</th><th>THÀNH TIỀN</th>${inspection ? "<th>ĐỐI CHIẾU</th>" : ""}</tr></thead><tbody>${order.items.map(item => {
    const ok = item.stock >= item.quantity || order.status === "completed";
    return `<tr class="${inspection ? (ok ? "check-row ok" : "check-row fail") : ""}"><td class="sku">${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.unit)}</td><td>${number.format(item.stock)}</td><td><b>${number.format(item.quantity)}</b></td><td class="number">${money.format(item.unit_price)}</td><td class="number"><b>${money.format(item.line_total)}</b></td>${inspection ? `<td>${ok ? '<span class="status completed">Đủ tồn</span>' : '<span class="status cancelled">Thiếu tồn</span>'}</td>` : ""}</tr>`;
  }).join("")}</tbody></table></div>`;
}

function detailHero(order, actions = "") {
  return `<div class="detail-hero"><div>${orderStatus(order)}<h1>${escapeHtml(order.code)}</h1><p>Ngày xuất ${isoDate(order.outbound_date)} · ${escapeHtml(order.customer_name)}</p></div><div class="detail-actions">${actions}</div></div>`;
}

async function changeStatus(id, status, message) {
  if (!await confirmAction(message)) return false;
  try { const result = await api(`/api/outbound-orders/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }); toast(result.message); return true; }
  catch (error) { handleError(error); return false; }
}

async function initOrderDetail() {
  const target = document.querySelector("#order-detail");
  try {
    const o = await api(`/api/outbound-orders/${entityId}`);
    const actions = '<a class="btn" href="/xuat-kho">Quay lại danh sách chỉ đọc</a>';
    target.innerHTML = `${detailHero(o, actions)}<div class="detail-grid"><section class="panel"><h2>Thông tin giao nhận</h2><dl class="info-list"><div><dt>Khách hàng</dt><dd>${escapeHtml(o.customer_name)}</dd></div><div><dt>Điện thoại</dt><dd>${escapeHtml(o.phone || "—")}</dd></div><div><dt>Mã số thuế</dt><dd>${escapeHtml(o.tax_code || "—")}</dd></div><div><dt>Địa chỉ</dt><dd>${escapeHtml(o.address || "—")}</dd></div><div><dt>Biển số xe</dt><dd>${escapeHtml(o.vehicle_no || "—")}</dd></div><div><dt>Container / Seal</dt><dd>${escapeHtml(o.container_no || "—")} / ${escapeHtml(o.seal_no || "—")}</dd></div></dl></section>
    <section class="panel"><h2>Tóm tắt</h2><dl class="summary"><div><dt>Số mặt hàng</dt><dd>${number.format(o.line_count)}</dd></div><div><dt>Tổng số lượng</dt><dd>${number.format(o.total_quantity)}</dd></div><div><dt>Tổng giá trị</dt><dd>${money.format(o.total_value)}</dd></div></dl><p><b>Ghi chú:</b> ${escapeHtml(o.note || "Không có")}</p></section></div>
    <section class="panel"><h2>Chi tiết hàng hóa</h2>${orderItemsTable(o)}</section>
    <section class="panel"><h2>Lịch sử xử lý</h2><div class="timeline">${o.history.map(historyItem).join("")}</div></section>`;
  } catch (error) { target.innerHTML = '<div class="panel empty">Không tìm thấy phiếu xuất.</div>'; handleError(error); }
}

function historyItem(item) {
  return `<article class="timeline-item"><span class="timeline-dot">✓</span><div class="timeline-card"><header><b>${escapeHtml(item.code || statusText[item.new_status] || item.new_status)}</b><time>${dateTime(item.created_at)}</time></header><p>${escapeHtml(item.note || "Cập nhật trạng thái")} · ${escapeHtml(item.actor)}</p>${item.code ? `<div class="subtext">${statusText[item.old_status] || "Khởi tạo"} → ${statusText[item.new_status] || item.new_status}</div>` : ""}</div></article>`;
}

async function initInspection() {
  const target = document.querySelector("#inspection-content");
  try {
    const [order, validation] = await Promise.all([api(`/api/outbound-orders/${entityId}`), api(`/api/outbound-orders/${entityId}/validate-stock`, { method: "POST" })]);
    const canComplete = validation.valid && validation.inspection_complete && order.status === "processing";
    const actions = `<a class="btn" href="/xuat-kho/${order.id}">Quay lại</a>${order.status === "pending" ? '<button class="btn primary" id="start-processing">Bắt đầu xử lý</button>' : ""}${order.status === "processing" ? `<button class="btn danger" id="cancel-order">Hủy phiếu</button><button class="btn success" id="complete-order" ${canComplete ? "" : "disabled"}>Hoàn thành & trừ tồn</button>` : ""}`;
    const oldInspection = Object.fromEntries(validation.inspections.map(item => [item.product_id, item]));
    const inspectionRows = order.items.map(item => {
      const old = oldInspection[item.product_id];
      const stockOk = item.stock >= item.quantity;
      return `<tr class="${stockOk ? "check-row ok" : "check-row fail"}" data-product="${item.product_id}">
        <td class="sku">${escapeHtml(item.sku)}</td><td>${escapeHtml(item.name)}</td><td>${number.format(item.stock)}</td><td><b>${number.format(item.quantity)}</b></td>
        <td><input class="actual-quantity" type="number" min="0" step="1" value="${old?.actual_quantity ?? item.quantity}" aria-label="Số lượng kiểm đếm ${escapeHtml(item.name)}" ${order.status !== "processing" ? "disabled" : ""}></td>
        <td><label><input class="condition-ok" type="checkbox" ${old ? (old.condition_ok ? "checked" : "") : "checked"} ${order.status !== "processing" ? "disabled" : ""}> Đạt</label></td>
        <td><input class="inspection-note" value="${escapeHtml(old?.note || "")}" maxlength="255" placeholder="Ghi chú" aria-label="Ghi chú ${escapeHtml(item.name)}" ${order.status !== "processing" ? "disabled" : ""}></td>
        <td>${stockOk ? '<span class="status completed">Đủ tồn</span>' : '<span class="status cancelled">Thiếu tồn</span>'}</td></tr>`;
    }).join("");
    const stockMessage = validation.valid ? "✓ Tồn hệ thống đủ cho toàn bộ phiếu." : `⚠ Có ${validation.shortages.length} mặt hàng không đủ tồn. Hãy điều chỉnh phiếu hoặc bổ sung kho.`;
    const inspectionMessage = validation.inspection_complete ? " Biên bản kiểm tra đã đạt." : " Cần lưu biên bản kiểm tra đạt trước khi hoàn thành.";
    target.innerHTML = `${detailHero(order, actions)}<div class="alert ${validation.valid && validation.inspection_complete ? "success" : "danger"}">${stockMessage}${inspectionMessage}</div><section class="panel"><div class="section-heading"><h2>Biên bản đối chiếu tồn kho</h2>${order.status === "processing" ? '<button class="btn primary small-btn" id="save-inspection">Lưu biên bản</button>' : ""}</div><div class="table-scroll"><table><thead><tr><th>SKU</th><th>SẢN PHẨM</th><th>TỒN HỆ THỐNG</th><th>SL PHẢI XUẤT</th><th>SL KIỂM ĐẾM</th><th>CHẤT LƯỢNG</th><th>GHI CHÚ</th><th>KẾT QUẢ TỒN</th></tr></thead><tbody id="inspection-rows">${inspectionRows}</tbody></table></div></section><section class="panel"><h2>Nguyên tắc xử lý</h2><p>Phiếu chỉ hoàn thành khi đủ tồn, kiểm đếm đúng số lượng và chất lượng của mọi dòng đều đạt. Tồn kho được trừ đúng một lần trong một giao dịch; mọi biến động được ghi vào sổ kiểm toán.</p></section>`;
    document.querySelector("#start-processing")?.addEventListener("click", async () => { if (await changeStatus(order.id, "processing", "Chuyển phiếu sang trạng thái đang xử lý?")) location.reload(); });
    document.querySelector("#cancel-order")?.addEventListener("click", async () => { if (await changeStatus(order.id, "cancelled", "Hủy phiếu xuất này?")) location.assign(`/xuat-kho/${order.id}`); });
    document.querySelector("#complete-order")?.addEventListener("click", async () => { if (await changeStatus(order.id, "completed", "Xác nhận hoàn thành? Tồn kho sẽ được trừ ngay và không thể hoàn tác.")) location.assign(`/xuat-kho/${order.id}`); });
    document.querySelector("#save-inspection")?.addEventListener("click", async () => {
      const items = [...document.querySelectorAll("#inspection-rows tr")].map(row => ({
        product_id: Number(row.dataset.product),
        actual_quantity: Number(row.querySelector(".actual-quantity").value),
        condition_ok: row.querySelector(".condition-ok").checked,
        note: row.querySelector(".inspection-note").value.trim(),
      }));
      if (items.some(item => !Number.isInteger(item.actual_quantity) || item.actual_quantity < 0)) { toast("Số lượng kiểm đếm phải là số nguyên không âm.", "error"); return; }
      try {
        const result = await api(`/api/outbound-orders/${order.id}/inspection`, { method: "PUT", body: JSON.stringify({ items }) });
        toast(result.passed ? "Biên bản đạt. Có thể hoàn thành phiếu." : "Đã lưu, nhưng biên bản chưa đạt.", result.passed ? "success" : "error");
        setTimeout(() => location.reload(), 500);
      } catch (error) { handleError(error); }
    });
  } catch (error) { target.innerHTML = '<div class="panel empty">Không thể kiểm tra phiếu xuất.</div>'; handleError(error); }
}

async function initHistory() {
  const target = document.querySelector("#history-list");
  try {
    const items = await api("/api/outbound-history");
    target.innerHTML = items.length ? items.map(historyItem).join("") : '<p class="empty">Chưa có lịch sử.</p>';
  } catch (error) { handleError(error); }
}

function initShell() {
  document.querySelector("#logout-button")?.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
      location.assign("/login");
    } catch (error) { handleError(error); }
  });
  const menu = document.querySelector("#menu-toggle"), sidebar = document.querySelector("#sidebar");
  menu?.addEventListener("click", () => { const open = sidebar.classList.toggle("open"); menu.setAttribute("aria-expanded", String(open)); });
  document.addEventListener("click", event => { if (innerWidth <= 820 && sidebar.classList.contains("open") && !sidebar.contains(event.target) && event.target !== menu) { sidebar.classList.remove("open"); menu.setAttribute("aria-expanded", "false"); } });
  document.querySelector("#global-search")?.addEventListener("submit", event => {
    event.preventDefault(); const query = document.querySelector("#global-query").value.trim();
    const destination = page.startsWith("order") || page === "inspection" || page === "history" ? "/xuat-kho" : "/hang-hoa";
    location.assign(`${destination}?q=${encodeURIComponent(query)}`);
  });
  document.querySelectorAll(".nav-item.disabled").forEach(button => button.onclick = () => toast("Chức năng này thuộc phân hệ của thành viên khác.", "error"));
}

document.addEventListener("DOMContentLoaded", async () => {
  if (page === "login") {
    const form = document.querySelector("#login-form");
    form?.addEventListener("submit", async event => {
      event.preventDefault();
      const alert = document.querySelector("#login-alert");
      alert.textContent = "";
      try {
        const result = await api("/api/auth/login", {
          method: "POST", body: JSON.stringify(fieldData(form)),
        });
        csrfToken = result.csrf_token;
        location.assign("/");
      } catch (error) { alert.textContent = error.message; }
    });
    return;
  }
  try {
    const sessionResult = await api("/api/auth/me");
    csrfToken = sessionResult.csrf_token;
  } catch (error) {
    handleError(error);
    return;
  }
  initShell();
  const init = {
    products: initProducts, "product-create": initProductForm, "product-edit": initProductForm,
    "product-detail": initProductDetail, orders: initOrders,
    "order-detail": initOrderDetail, history: initHistory,
  }[page];
  try { await init?.(); } catch (error) { handleError(error); }
});
