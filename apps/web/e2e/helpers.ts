import { expect, type Page } from "@playwright/test";

export async function enterDemo(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /Enter demo/ }).click();
  await expect(page.getByRole("heading", { name: /What do you want to map/ })).toBeVisible();
}

/** Creates a fresh synthetic graph through the UI so tests never depend on each other's state. */
export async function createExampleGraph(page: Page, example = "Series B product leaders", name?: string, reviewEverything = true) {
  await page.goto("/new");
  await page.getByRole("button", { name: new RegExp(example) }).click();
  await expect(page.getByRole("heading", { name: "Charter", exact: true })).toBeVisible();
  if (name) await page.getByLabel("Name", { exact: true }).fill(name);
  if (reviewEverything) await page.getByRole("radio", { name: /Review everything/ }).click();
  await page.getByRole("button", { name: /Create graph/ }).click();
  await expect(page.getByRole("heading", { name: "Nothing mapped yet" })).toBeVisible();
  return page.url().split("/g/")[1].split(/[/?]/)[0];
}

export async function startSimulation(page: Page) {
  await page.getByRole("button", { name: /Start research|Run/ }).first().click();
  await page.getByRole("button", { name: "Start simulation" }).click();
}

export function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("Failed to load resource")) errors.push(m.text()); });
  return errors;
}
