import { expect, test } from "@playwright/test";
import { enterDemo } from "./helpers";

test("mobile: browse by list, open an evidence sheet, no horizontal overflow", async ({ page }) => {
  await enterDemo(page);
  for (const path of ["/", "/settings"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${path} overflows`).toBe(true);
  }
  await page.goto("/");
  await page.getByRole("link", { name: "Open Series B product leaders" }).click();
  await expect(page.getByLabel("Filter entities")).toBeVisible(); // the list is the primary way to browse
  await page.getByLabel("Filter entities").fill("Quillon");
  await page.getByRole("button", { name: /Quillon Data/ }).click();
  const sheet = page.getByLabel("Evidence inspector");
  await expect(sheet.getByRole("heading", { name: "Quillon Data" })).toBeVisible();
  await expect(sheet.getByText("Contested").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: /^Review/ }).click();
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});
