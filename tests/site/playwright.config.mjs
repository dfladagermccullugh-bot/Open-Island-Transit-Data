import {defineConfig, devices} from "@playwright/test";
import {BASE, PORT} from "./serve.mjs";

// The site is checked at a desk and on a phone, in the light and the dark
// theme. The phone uses an iPhone 13's screen and touch settings on Chromium,
// the one browser the tests install.
const phone = {...devices["iPhone 13"], browserName: "chromium", defaultBrowserType: "chromium"};

// SITE_URL points the tests at a site that is already being served, such as
// the published one (scripts/test_site.sh live). Without it the tests serve
// site/_site themselves.
const live = process.env.SITE_URL ? new URL("./", process.env.SITE_URL).href : null;

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.mjs",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: process.env.CI ? [["list"], ["html", {open: "never"}]] : "list",
  timeout: 30_000,
  expect: {timeout: 10_000},
  use: {
    baseURL: live ?? `http://127.0.0.1:${PORT}${BASE}`,
    trace: "off",
    locale: "en-US",
    timezoneId: "America/Los_Angeles",
    ...(process.env.PW_CHROMIUM ? {launchOptions: {executablePath: process.env.PW_CHROMIUM}} : {}),
  },
  ...(live ? {} : {
    webServer: {
      command: "node serve.mjs",
      url: `http://127.0.0.1:${PORT}${BASE}index.html`,
      reuseExistingServer: !process.env.CI,
      timeout: 20_000,
    },
  }),
  projects: [
    {name: "desktop-light", use: {viewport: {width: 1280, height: 900}, colorScheme: "light"}},
    {name: "desktop-dark", use: {viewport: {width: 1280, height: 900}, colorScheme: "dark"}},
    {name: "phone-light", use: {...phone, colorScheme: "light"}},
    {name: "phone-dark", use: {...phone, colorScheme: "dark"}},
  ],
});
