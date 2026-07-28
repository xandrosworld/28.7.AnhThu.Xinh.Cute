const { defineConfig } = require("@playwright/test");

const chromiumExecutable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

const viewports = [
  { name: "1366", viewport: { width: 1366, height: 768 } },
  { name: "1024", viewport: { width: 1024, height: 768 } },
  { name: "390", viewport: { width: 390, height: 844 } },
];

const browsers = [
  {
    name: "chromium",
    use: {
      browserName: "chromium",
      ...(chromiumExecutable
        ? { launchOptions: { executablePath: chromiumExecutable } }
        : {}),
    },
  },
  { name: "firefox", use: { browserName: "firefox" } },
];

module.exports = defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : "line",
  use: {
    baseURL: "http://127.0.0.1:5000",
    locale: "vi-VN",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: browsers.flatMap((browser) =>
    viewports.map((size) => ({
      name: `${browser.name}-${size.name}`,
      use: { ...browser.use, viewport: size.viewport },
    })),
  ),
  webServer: {
    command: "python scripts/e2e_server.py",
    url: "http://127.0.0.1:5000/",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
