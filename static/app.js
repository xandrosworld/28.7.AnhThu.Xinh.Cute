"use strict";

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const money = value => `${new Intl.NumberFormat("vi-VN").format(Number(value || 0))} ₫`;
const number = value => new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(Number(value || 0));
const dateTime = value => value ? new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "—";
const statusLabels = { draft: "Bản nháp", pending: "Chờ kiểm tra", inspecting: "Đã kiểm tra", completed: "Hoàn tất", rejected: "Không đạt" };

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), 3800);
}

function loading(show) { $("#loading").hidden = !show; }

async function api(path, options = {}) {
  loading(true);
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const isJson = response.headers.get("content-type")?.includes("json");
    const body = isJson ? await response.json() : await response.text();
    if (!response.ok) throw new Error(body.error || "Không thể xử lý yêu cầu.");
    return body;
  } catch (error) {
    toast(error.message || "Không thể kết nối máy chủ.", "error");
    throw error;
  } finally { loading(false); }
}

function statusBadge(status) {
  return `<span class="status status-${escapeHtml(status)}">${statusLabels[status] || status}</span>`;
}

function initShell() {
  const menu = $("#menu-toggle");
  menu?.addEventListener("click", () => {
    const opened = $("#sidebar").classList.toggle("open");
    menu.setAttribute("aria-expanded", String(opened));
  });
  $("#global-search")?.addEventListener("keydown", event => {
    if (event.key === "Enter" && event.target.value.trim()) {
      location.href = `/receipts?q=${encodeURIComponent(event.target.value.trim())}`;
    }
  });
}

async function initDashboard() {
  const data = await api("/api/dashboard");
  Object.entries(data.kpis).forEach(([key, value]) => {
    const el = $(`[data-kpi="${key}"]`);
    if (el) el.textContent = number(value);
  });
  const values = data.monthly.map(row => Number(row.quantity));
  const max = Math.max(...values, 1);
  $("#monthly-chart").innerHTML = data.monthly.length
    ? data.monthly.map(row => `<div class="bar-item"><div class="bar" data-value="${number(row.quantity)}" style="height:${Math.max(4, Number(row.quantity) / max * 100)}%"></div><small>${escapeHtml(row.month)}</small></div>`).join("")
    : `<p class="empty">Chưa có dữ liệu nhập kho theo tháng.</p>`;
  const catMax = Math.max(...data.categories.map(row => Number(row.quantity)), 1);
  $("#category-chart").innerHTML = data.categories.map(row => `<div class="category-row"><span>${escapeHtml(row.category)}</span><div class="meter"><i style="width:${Number(row.quantity) / catMax * 100}%"></i></div><b>${number(row.quantity)}</b></div>`).join("");
  $("#activity-list").innerHTML = data.activity.map(row => `<li><strong>${escapeHtml(row.details)}</strong><time>${dateTime(row.created_at)}</time></li>`).join("") || `<li>Chưa có hoạt động.</li>`;
  $("#stock-alerts").innerHTML = data.alerts.map(row => `<div class="stock-alert"><span><strong>${escapeHtml(row.sku)}</strong><small>${escapeHtml(row.name)}</small></span><b>${number(row.current_stock)} ${escapeHtml(row.unit)}</b></div>`).join("") || `<p class="empty">Tồn kho đang ở mức an toàn.</p>`;
}

async function initReceipts() {
  const form = $("#receipt-filters");
  const urlParams = new URLSearchParams(location.search);
  if (urlParams.get("q")) form.elements.q.value = urlParams.get("q");
  async function load() {
    const params = new URLSearchParams(new FormData(form));
    const rows = await api(`/api/receipts?${params}`);
    $("#receipt-rows").innerHTML = rows.map(row => `<tr>
      <td><a href="/receipts/${row.id}"><strong>${escapeHtml(row.code)}</strong></a></td>
      <td>${escapeHtml(row.supplier)}</td><td>${escapeHtml(row.warehouse)}</td><td>${dateTime(row.received_date)}</td>
      <td>${number(row.item_count)}</td><td>${number(row.actual_total || row.planned_total)}</td><td>${statusBadge(row.status)}</td>
      <td class="actions"><div class="table-actions"><a class="icon-button" href="/receipts/${row.id}" title="Xem chi tiết" aria-label="Xem ${escapeHtml(row.code)}">⌕</a>
      ${row.status !== "completed" ? `<a class="icon-button" href="/receipts/${row.id}/edit" title="Chỉnh sửa" aria-label="Sửa ${escapeHtml(row.code)}">✎</a><a class="icon-button" href="/receipts/${row.id}/inspect" title="Kiểm tra" aria-label="Kiểm tra ${escapeHtml(row.code)}">✓</a><button class="icon-button danger delete-receipt" data-id="${row.id}" data-code="${escapeHtml(row.code)}" title="Xóa" aria-label="Xóa ${escapeHtml(row.code)}">×</button>` : ""}
      </div></td></tr>`).join("");
    $("#receipt-empty").hidden = rows.length > 0;
  }
  form.addEventListener("submit", event => { event.preventDefault(); load(); });
  form.addEventListener("reset", () => setTimeout(load));
  $("#receipt-rows").addEventListener("click", async event => {
    const button = event.target.closest(".delete-receipt");
    if (!button || !confirm(`Xóa phiếu ${button.dataset.code}? Thao tác này không thể hoàn tác.`)) return;
    await api(`/api/receipts/${button.dataset.id}`, { method: "DELETE" });
    toast("Đã xóa phiếu nhập.");
    load();
  });
  await load();
}

async function initReceiptForm() {
  const form = $("#receipt-form");
  const receiptId = document.body.dataset.receiptId;
  const products = await api("/api/products");
  const template = $("#item-template");
  function addItem(item = {}) {
    const fragment = template.content.cloneNode(true);
    const row = $("tr", fragment);
    const select = $(".product-select", row);
    select.innerHTML += products.map(p => `<option value="${p.id}">${escapeHtml(p.sku)} · ${escapeHtml(p.name)}</option>`).join("");
    select.value = item.product_id || "";
    $(".qty-input", row).value = item.planned_qty || 1;
    $(".price-input", row).value = item.unit_price ?? 0;
    updateRow(row);
    $("#item-rows").append(row);
  }
  function updateRow(row) {
    const product = products.find(p => String(p.id) === $(".product-select", row).value);
    if (product) {
      $(".unit-cell", row).textContent = product.unit;
      if (!$(".price-input", row).value || Number($(".price-input", row).value) === 0) $(".price-input", row).value = product.unit_price;
    } else $(".unit-cell", row).textContent = "—";
    $(".line-total", row).textContent = money(Number($(".qty-input", row).value) * Number($(".price-input", row).value));
    $("#grand-total").textContent = money($$("#item-rows tr").reduce((sum, current) => sum + Number($(".qty-input", current).value) * Number($(".price-input", current).value), 0));
  }
  $("#add-item").addEventListener("click", () => addItem());
  $("#item-rows").addEventListener("input", event => updateRow(event.target.closest("tr")));
  $("#item-rows").addEventListener("change", event => updateRow(event.target.closest("tr")));
  $("#item-rows").addEventListener("click", event => {
    if (!event.target.closest(".remove-item")) return;
    if ($$("#item-rows tr").length === 1) return toast("Phiếu phải có ít nhất một mặt hàng.", "error");
    event.target.closest("tr").remove();
    updateRow($("#item-rows tr"));
  });
  if (receiptId) {
    const receipt = await api(`/api/receipts/${receiptId}`);
    for (const name of ["supplier", "warehouse", "vehicle_no", "container_no", "note"]) form.elements[name].value = receipt[name] || "";
    form.elements.received_date.value = receipt.received_date.slice(0, 16);
    receipt.items.forEach(addItem);
  } else {
    const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
    form.elements.received_date.value = now.toISOString().slice(0, 16);
    addItem();
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const items = $$("#item-rows tr").map(row => ({
      product_id: $(".product-select", row).value,
      planned_qty: $(".qty-input", row).value,
      unit_price: $(".price-input", row).value,
    }));
    if (new Set(items.map(item => item.product_id)).size !== items.length) return toast("Không được chọn trùng mặt hàng.", "error");
    const payload = Object.fromEntries(new FormData(form));
    payload.items = items;
    const result = await api(receiptId ? `/api/receipts/${receiptId}` : "/api/receipts", {
      method: receiptId ? "PUT" : "POST", body: JSON.stringify(payload),
    });
    toast(result.message);
    location.href = receiptId ? `/receipts/${receiptId}` : `/receipts/${result.id}`;
  });
}

async function initReceiptDetail() {
  const id = document.body.dataset.receiptId;
  const data = await api(`/api/receipts/${id}`);
  $("#detail-code").textContent = data.code;
  $("#detail-subtitle").textContent = `Tạo lúc ${dateTime(data.created_at)} · cập nhật ${dateTime(data.updated_at)}`;
  $("#detail-supplier").textContent = data.supplier; $("#detail-warehouse").textContent = data.warehouse;
  $("#detail-date").textContent = dateTime(data.received_date); $("#detail-status").innerHTML = statusBadge(data.status);
  $("#detail-vehicle").textContent = data.vehicle_no || "—"; $("#detail-container").textContent = data.container_no || "—";
  $("#detail-items").innerHTML = data.items.map(item => `<tr><td><strong>${escapeHtml(item.sku)}</strong></td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.unit)}</td><td>${number(item.planned_qty)}</td><td>${item.actual_qty ? number(item.actual_qty) : "—"}</td><td>${money(item.unit_price)}</td><td>${money((item.actual_qty || item.planned_qty) * item.unit_price)}</td></tr>`).join("");
  const inspectionText = data.inspection ? `\n\nKết quả kiểm tra: ${data.inspection.result === "pass" ? "Đạt" : "Không đạt"} · ${dateTime(data.inspection.inspected_at)}\n${data.inspection.note || "Không có biên bản bất thường."}` : "\n\nPhiếu chưa được kiểm tra.";
  $("#detail-note").textContent = (data.note || "Không có ghi chú chứng từ.") + inspectionText;
  $("#edit-link").href = `/receipts/${id}/edit`; $("#inspect-link").href = `/receipts/${id}/inspect`;
  if (data.status === "completed") { $("#edit-link").hidden = true; $("#inspect-link").hidden = true; }
}

async function initInspection() {
  const id = document.body.dataset.receiptId;
  const form = $("#inspection-form");
  const data = await api(`/api/receipts/${id}`);
  $("#inspection-code").textContent = `${data.code} · ${data.supplier}`;
  $("#transport-info").textContent = `${data.vehicle_no || "Chưa có biển số"} · ${data.container_no || "Không có container"}`;
  $("#inspection-summary").innerHTML = `<div><span>Kho nhận</span><strong>${escapeHtml(data.warehouse)}</strong></div><div><span>Ngày nhập</span><strong>${dateTime(data.received_date)}</strong></div>`;
  $("#inspection-items").innerHTML = data.items.map(item => `<tr><td><strong>${escapeHtml(item.sku)}</strong></td><td>${escapeHtml(item.name)}</td><td>${number(item.planned_qty)} ${escapeHtml(item.unit)}</td><td><input class="actual-qty" data-item-id="${item.id}" type="number" min="0.01" step="0.01" required value="${item.actual_qty || item.planned_qty}" aria-label="Số lượng thực nhập ${escapeHtml(item.sku)}"></td></tr>`).join("");
  if (data.inspection) {
    Object.entries(data.inspection.checklist).forEach(([key, value]) => { const input = $(`input[name="${key}"][value="${value}"]`); if (input) input.checked = true; });
    $(`input[name="overall"][value="${data.inspection.result}"]`).checked = true;
    form.elements.note.value = data.inspection.note || "";
  }
  function progress() { $("#check-progress").textContent = `${$$(".check-row input:checked").length}/7 tiêu chí`; }
  $("#checklist").addEventListener("change", progress); progress();
  async function saveInspection() {
    if (!form.reportValidity()) throw new Error("Vui lòng điền đủ thông tin kiểm tra.");
    const checklist = {};
    for (const row of $$(".check-row")) {
      const checked = $("input:checked", row);
      if (!checked) throw new Error("Vui lòng đánh giá đủ 7 tiêu chí.");
      checklist[row.dataset.key] = checked.value;
    }
    const overall = $('input[name="overall"]:checked');
    if (!overall) throw new Error("Vui lòng chọn kết quả tổng thể.");
    const actual_quantities = Object.fromEntries($$(".actual-qty").map(input => [input.dataset.itemId, input.value]));
    return api(`/api/receipts/${id}/inspection`, { method: "POST", body: JSON.stringify({ checklist, result: overall.value, note: form.elements.note.value, actual_quantities }) });
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    try { const result = await saveInspection(); toast(result.message); } catch (error) { if (!error.message.includes("Không thể")) toast(error.message, "error"); }
  });
  $("#complete-receipt").addEventListener("click", async () => {
    try {
      await saveInspection();
      const result = await api(`/api/receipts/${id}/complete`, { method: "POST" });
      toast(result.message);
      setTimeout(() => location.href = `/receipts/${id}`, 650);
    } catch (error) { if (!error.message.includes("Không thể")) toast(error.message, "error"); }
  });
  if (data.status === "completed") {
    $$("input,textarea,button", form).forEach(el => el.disabled = true);
    toast("Phiếu này đã hoàn tất và được khóa chỉnh sửa.");
  }
}

async function initHistory() {
  const form = $("#history-filters");
  async function load() {
    const params = new URLSearchParams(new FormData(form));
    const data = await api(`/api/history?${params}`);
    const icons = { CREATE: "+", UPDATE: "✎", INSPECT: "✓", COMPLETE: "↓", DELETE: "×" };
    $("#history-list").innerHTML = data.map(row => `<li><span class="history-icon">${icons[row.action] || "•"}</span><span><strong>${escapeHtml(row.details)}</strong><small>${escapeHtml(row.action)} · ${escapeHtml(row.entity_type)} #${row.entity_id || "—"}</small></span><time>${dateTime(row.created_at)}</time></li>`).join("");
    $("#history-empty").hidden = data.length > 0;
  }
  form.addEventListener("submit", event => { event.preventDefault(); load(); });
  await load();
}

async function initReports() {
  const form = $("#report-filters");
  const end = new Date();
  const start = new Date(end.getFullYear(), end.getMonth(), 1);
  form.elements.start.value = start.toISOString().slice(0, 10);
  form.elements.end.value = end.toISOString().slice(0, 10);
  async function load() {
    if (form.elements.start.value > form.elements.end.value) return toast("Từ ngày không được sau đến ngày.", "error");
    const params = new URLSearchParams(new FormData(form));
    const data = await api(`/api/reports?${params}`);
    for (const [key, value] of Object.entries(data.summary)) {
      const el = $(`[data-report="${key}"]`);
      if (el) el.textContent = key === "total_value" ? money(value) : number(value);
    }
    $("#report-period").textContent = `Từ ${data.start} đến ${data.end}`;
    $("#report-rows").innerHTML = data.receipts.map(row => `<tr><td><strong>${escapeHtml(row.code)}</strong></td><td>${escapeHtml(row.supplier)}</td><td>${escapeHtml(row.warehouse)}</td><td>${dateTime(row.completed_at)}</td><td>${number(row.quantity)}</td><td>${money(row.value)}</td></tr>`).join("");
    $("#report-empty").hidden = data.receipts.length > 0;
    $("#top-products").innerHTML = data.top_products.map(row => `<li><strong>${escapeHtml(row.sku)}</strong><small>${escapeHtml(row.name)} · ${number(row.quantity)} ${escapeHtml(row.unit)}</small></li>`).join("") || `<li>Chưa có dữ liệu.</li>`;
    $("#export-report").href = `/reports/export.csv?${params}`;
  }
  form.addEventListener("submit", event => { event.preventDefault(); load(); });
  await load();
}

document.addEventListener("DOMContentLoaded", async () => {
  initShell();
  const initializers = {
    dashboard: initDashboard, receipts: initReceipts, "receipt-form": initReceiptForm,
    "receipt-detail": initReceiptDetail, inspection: initInspection, history: initHistory, reports: initReports,
  };
  try { await initializers[document.body.dataset.page]?.(); } catch (error) { console.error(error); }
});
