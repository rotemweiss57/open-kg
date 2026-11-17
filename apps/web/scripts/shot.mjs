// Usage: node scripts/shot.mjs <path> <out.png> [width=1440] [height=900] [theme=dark] [actions-json]
// Signs into the demo workspace, opens the path, optionally clicks/waits, and screenshots.
import { chromium } from "@playwright/test";

const [path = "/", out = "shot.png", w = "1440", h = "900", theme = "dark", actions = "[]"] = process.argv.slice(2);
const base = process.env.BASE_URL ?? "http://localhost:3100";
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1, colorScheme: theme });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("PAGEERROR " + e.message));
await page.addInitScript((t) => { try { localStorage.setItem("openkg-theme", t); } catch {} }, theme);
if (path !== "/login") {
  await page.goto(base + "/login");
  await page.evaluate(() => fetch("/api/v1/auth/demo", { method: "POST" }));
}
let target = path;
if (/\{(sb|bat)\}/.test(path)) {
  const graphs = await page.evaluate(() => fetch("/api/v1/graphs").then((r) => r.json()));
  target = path.replace("{sb}", graphs.find((g) => g.name.startsWith("Series"))?.id).replace("{bat}", graphs.find((g) => g.name.startsWith("Solid"))?.id);
}
await page.goto(base + target, { waitUntil: "domcontentloaded" }).catch(() => {});
await page.waitForTimeout(1800);
await page.waitForTimeout(900);
for (const a of JSON.parse(actions)) {
  if (a.click) await page.locator(a.click).first().click({ timeout: 8000 });
  if (a.text) await page.getByText(a.text, { exact: a.exact ?? false }).first().click({ timeout: 8000 });
  if (a.role) await page.getByRole(a.role, { name: a.name }).first().click({ timeout: 8000 });
  if (a.fill) await page.locator(a.fill).first().fill(a.value);
  if (a.press) await page.keyboard.press(a.press);
  if (a.wait) await page.waitForTimeout(a.wait);
}
await page.screenshot({ path: out, fullPage: process.env.FULL === "1" });
if (errors.length) console.log("CONSOLE ERRORS:\n" + [...new Set(errors)].slice(0, 8).join("\n"));
console.log("saved", out);
await browser.close();
