const { test, expect } = require("@playwright/test");
const crypto = require("node:crypto");

const users = {
  admin: ["admin", "Admin@123"],
  cs: ["cs", "Cs@123456"],
  warehouse: ["warehouse", "Kho@12345"],
};

async function login(page, role) {
  const [username, password] = users[role];
  await page.goto("/");
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: "Đăng nhập" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.locator("#dashboard-stats .stat-card strong")).toHaveCount(6);
}

async function expectNoHorizontalPageOverflow(page) {
  const metrics = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll("body *")]
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          selector: `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ""}${element.classList.length ? `.${[...element.classList].join(".")}` : ""}`,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          clippedByTableWrap: Boolean(element.closest(".table-wrap")),
        };
      })
      .filter((item) => item.right > viewport + 1 && !item.clippedByTableWrap)
      .slice(0, 10);
    return {
      overflow: document.body.scrollWidth - viewport,
      bodyScrollWidth: document.body.scrollWidth,
      offenders,
      containers: (() => {
        const result = [];
        let element = document.querySelector("table");
        while (element && result.length < 6) {
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          result.push({
            selector: `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ""}${element.classList.length ? `.${[...element.classList].join(".")}` : ""}`,
            width: Math.round(rect.width),
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
            overflowX: style.overflowX,
          });
          element = element.parentElement;
        }
        return result;
      })(),
    };
  });
  expect(
    metrics.overflow,
    `Trang tràn ngang ${metrics.overflow}px: ${JSON.stringify(metrics.offenders)} containers=${JSON.stringify(metrics.containers)}`,
  ).toBeLessThanOrEqual(1);
}

test.beforeEach(async ({ page }) => {
  const failures = [];
  page.on("pageerror", (error) => failures.push(`JavaScript: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 500) {
      failures.push(`HTTP ${response.status()}: ${response.url()}`);
    }
  });
  page.__wmsFailures = failures;
});

test.afterEach(async ({ page }) => {
  expect(page.__wmsFailures || []).toEqual([]);
});

test("auth, dashboard and responsive navigation", async ({ page }, testInfo) => {
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/(?:index\.html)?$/);
  await expect(page.locator("#login-form")).toBeVisible();

  await page.locator("#username").fill("admin");
  await page.locator("#password").fill("khong-dung");
  await page.getByRole("button", { name: "Đăng nhập" }).click();
  await expect(page.locator("#login-alert")).toBeVisible();
  await expect(page.locator("#login-alert")).not.toBeEmpty();

  await page.locator("#password").fill(users.admin[1]);
  await page.getByRole("button", { name: "Đăng nhập" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.locator("main#main-content")).toBeVisible();
  await expect(page.locator("#dashboard-stats .stat-card strong")).toHaveCount(6);
  await expectNoHorizontalPageOverflow(page);

  const menu = page.locator("#menu-toggle");
  const sidebar = page.locator("#sidebar");
  if (testInfo.project.use.viewport.width === 390) {
    await expect(menu).toBeVisible();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await menu.click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await expect(sidebar).toHaveClass(/\bopen\b/);
    await expect(sidebar.getByRole("link", { name: "Tổng quan" })).toBeVisible();
  } else {
    await expect(menu).toBeHidden();
    await expect(sidebar).toBeVisible();
  }
});

test("server roles and role-specific navigation", async ({ page }) => {
  const expectations = {
    admin: { products: 1, stocktakes: 1, users: 1, usersStatus: 200 },
    cs: { products: 1, stocktakes: 0, users: 0, usersStatus: 403 },
    warehouse: { products: 0, stocktakes: 1, users: 0, usersStatus: 403 },
  };

  for (const [role, expected] of Object.entries(expectations)) {
    await login(page, role);
    const nav = page.locator(".nav-list");
    await expect(nav.locator('a[href="/products"]')).toHaveCount(expected.products);
    await expect(nav.locator('a[href="/stocktakes"]')).toHaveCount(
      expected.stocktakes,
    );
    await expect(
      nav.locator('a[href="/users"], a[href="/quanlynguoidung.html"]'),
    ).toHaveCount(expected.users);

    const response = await page.goto("/users");
    expect(response.status()).toBe(expected.usersStatus);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Đăng xuất" }).click();
    await expect(page).toHaveURL(/\/$/);
  }
});

test("admin validates and creates a category", async ({ page }, testInfo) => {
  await login(page, "admin");
  await page.goto("/categories");
  await expect(page.locator("#category-body tr").first()).toBeVisible();

  await page.locator("#category-add").click();
  const modal = page.locator("#category-modal");
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "Lưu danh mục" }).click();
  expect(
    await page.locator("#category-code").evaluate((element) => element.validity.valid),
  ).toBe(false);

  const viewport = testInfo.project.use.viewport.width;
  const browser = testInfo.project.use.browserName.slice(0, 2).toUpperCase();
  const suffix = crypto.randomUUID().replaceAll("-", "").slice(0, 5).toUpperCase();
  const code = `E2E${browser}${viewport}${suffix}`;
  await page.locator("#category-code").fill(code);
  await page.locator("#category-name").fill(
    `Danh mục E2E ${testInfo.project.name}`,
  );
  await page.locator("#category-description").fill(
    "Dữ liệu cô lập được tạo bởi Playwright smoke test.",
  );
  await modal.getByRole("button", { name: "Lưu danh mục" }).click();

  await expect(modal).toBeHidden();
  await expect(page.locator("#category-body tr").filter({ hasText: code })).toHaveCount(
    1,
  );
  await expectNoHorizontalPageOverflow(page);
});
