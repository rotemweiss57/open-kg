import { expect, test } from "@playwright/test";
import { createExampleGraph, enterDemo, startSimulation, trackErrors } from "./helpers";

test.describe.configure({ mode: "serial" });

test("unauthenticated visitors are sent to sign in", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("library lists the labelled synthetic graphs with real counts", async ({ page }) => {
  const errors = trackErrors(page);
  await enterDemo(page);
  const card = page.locator("div", { has: page.getByRole("link", { name: "Open Series B product leaders" }) }).first();
  await expect(card).toBeVisible();
  await expect(page.getByText("Synthetic").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Solid-state battery supply chain" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("create a graph, watch a run survive a refresh, review, and see the graph change", async ({ page }) => {
  const errors = trackErrors(page);
  await enterDemo(page);
  const gid = await createExampleGraph(page, "Series B product leaders", "E2E journey graph");

  await startSimulation(page);
  await expect(page).toHaveURL(new RegExp(`/g/${gid}\\?run=`));
  // provisional discoveries bloom onto the canvas while the run is live
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Live research activity")).toBeVisible();

  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: "Research" }).click();
  const log = page.getByRole("log", { name: "Research activity" });
  await expect(log.getByText("Research plan")).toBeVisible();
  const before = await log.locator("li").count();
  expect(before).toBeGreaterThan(3);

  // closing/refreshing the tab loses nothing: the run continues server-side and events replay
  await page.reload();
  await expect(log.getByText("Research plan")).toBeVisible();
  expect(await log.locator("li").count()).toBeGreaterThanOrEqual(before);
  await expect(page.getByText("Awaiting review").first()).toBeVisible({ timeout: 80_000 });
  await expect(log.getByText("Summary")).toBeVisible();
  await expect(page.getByText("Simulation", { exact: true }).first()).toBeVisible();

  // review: approving changes the graph, rejecting leaves an auditable record
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: /^Review/ }).click();
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
  // the inbox offers one-click approval for well-evidenced items and bundles the rest by entity
  await expect(page.getByText(/ha(ve|s) enough evidence/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve \d+/ }).first()).toBeVisible();
  await page.getByRole("tab", { name: "By type" }).click();
  await page.getByRole("button", { name: /Conflict on Quillon Data/ }).click();
  await expect(page.getByText("Current", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified quote").first()).toBeVisible();
  await page.getByRole("button", { name: /Add Company: Pallas Freight/ }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(page.getByText("Rejected", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /Add Company: Northwind Robotics/ }).click();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText("Approved", { exact: true }).first()).toBeVisible();
  await page.getByRole("tab", { name: "Decided" }).click();
  await expect(page.getByText("Pallas Freight").first()).toBeVisible();
  await expect(page.getByText("Rejected").first()).toBeVisible();

  // the inspector shows evidence, time qualifiers and status for nodes and edges
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: "Graph", exact: true }).click();
  await page.getByLabel("Find an entity in this graph").fill("Northwind");
  await page.getByRole("option", { name: /Northwind Robotics/ }).click();
  const inspector = page.getByLabel("Evidence inspector");
  await expect(inspector.getByRole("heading", { name: "Northwind Robotics" })).toBeVisible();
  await expect(inspector.getByText("Verified").first()).toBeVisible();
  await expect(inspector.getByText("Funding stage")).toBeVisible();
  await expect(inspector.getByText(/since Feb 11, 2026/).first()).toBeVisible();
  await inspector.getByRole("button", { name: /Inspect relationship with Halcyon Ventures/ }).click();
  await expect(inspector.getByRole("heading", { name: /^Evidence/ })).toBeVisible();
  await expect(inspector.getByText("Provisional").first()).toBeVisible();
  await expect(inspector.getByText(/Synthetic/).first()).toBeVisible();

  // state is persisted: a hard reload restores the same selection and graph
  await page.reload();
  await expect(page.getByLabel("Evidence inspector").getByRole("heading", { name: /^Evidence/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test("two graphs stay separate", async ({ page }) => {
  await enterDemo(page);
  await page.getByRole("link", { name: "Open Solid-state battery supply chain" }).click();
  await expect(page.locator('.react-flow__node[aria-label="Company Veridian Cells"]')).toBeVisible();
  await expect(page.locator('.react-flow__node[aria-label="Company Northwind Robotics"]')).toHaveCount(0);
  await page.getByRole("link", { name: "Back to library" }).click();
  await page.getByRole("link", { name: "Open Series B product leaders" }).click();
  await expect(page.locator('.react-flow__node[aria-label="Company Northwind Robotics"]')).toBeVisible();
  await expect(page.locator('.react-flow__node[aria-label="Company Veridian Cells"]')).toHaveCount(0);
});

test("pause, resume and cancel a run from mission control", async ({ page }) => {
  await enterDemo(page);
  await createExampleGraph(page, "Solid-state battery supply chain", "E2E control graph");
  await startSimulation(page);
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: "Research" }).click();
  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByText("Paused").first()).toBeVisible();
  const log = page.getByRole("log", { name: "Research activity" });
  const frozen = await log.locator("li").count();
  await page.waitForTimeout(2500);
  expect(await log.locator("li").count()).toBe(frozen); // nothing new starts while paused
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("Cancelled").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "New run" })).toBeVisible();
});

test("monitor detects a simulated change with before/after evidence", async ({ page }) => {
  await enterDemo(page);
  await page.getByRole("link", { name: "Open Series B product leaders" }).click();
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: /^Changes/ }).click();
  await expect(page.getByRole("heading", { name: "Changes", exact: true })).toBeVisible();
  const change = page.getByRole("listitem").filter({ hasText: "Elena Vasquez" }).filter({ hasText: "Before" }).first();
  await expect(change.getByText("Head of Product", { exact: true })).toBeVisible();
  await expect(change.getByText("Chief Product Officer", { exact: true })).toBeVisible();
  await expect(change.getByText(/Tessellate Health promotes Elena Vasquez/).first()).toBeVisible();
  // an unchanged source produces no alert
  const priya = page.getByRole("listitem").filter({ hasText: "Priya Raman" }).filter({ hasText: "Check now" });
  await priya.getByRole("button", { name: /Check now/ }).click();
  await expect(page.getByText("No change", { exact: true })).toBeVisible();
});

test("keyboard: command palette, theme and settings", async ({ page }) => {
  await enterDemo(page);
  await page.keyboard.press("ControlOrMeta+k");
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await expect(palette).toBeVisible();
  await page.keyboard.type("Quillon");
  await expect(palette.getByText("Quillon Data").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Evidence inspector").getByRole("heading", { name: "Quillon Data" })).toBeVisible();
  await expect(page.getByLabel("Evidence inspector").getByText("Contested").first()).toBeVisible();
  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type("settings");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByText("Demo mode is on")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Providers/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tavily" })).toBeVisible(); // works with or without keys configured
  const before = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.getByRole("button", { name: "Toggle theme" }).click();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).not.toBe(before);
});

test("evidence policy, table view and cited brief", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await enterDemo(page);
  await createExampleGraph(page, "Series B product leaders", "E2E evidence graph", false); // default: approve on evidence
  await startSimulation(page);
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: "Research" }).click();
  await expect(page.getByRole("log", { name: "Research activity" }).getByText("Approved").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Awaiting review").first()).toBeVisible({ timeout: 80_000 });
  await page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name: "Table" }).click();
  await page.getByRole("tab", { name: "Company" }).click();
  const row = page.getByRole("row").filter({ hasText: "Northwind Robotics" });
  await expect(row).toContainText("Series B");
  await expect(row).toContainText("Halcyon Ventures");
  await page.getByLabel("Verified only").check();
  await expect(page.getByRole("row").filter({ hasText: "Northwind Robotics" })).toBeVisible(); // two sources: auto-approved
  await expect(page.getByRole("row").filter({ hasText: "Quillon Data" })).toHaveCount(0); // single source: still provisional
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV" }).click();
  expect((await download).suggestedFilename()).toMatch(/company\.csv$/);
  await page.getByRole("row").filter({ hasText: "Northwind Robotics" }).getByRole("button", { name: "Northwind Robotics" }).click();
  await page.getByRole("button", { name: /Copy brief/ }).click();
  const brief = await page.evaluate(() => navigator.clipboard.readText());
  expect(brief).toContain("# Northwind Robotics");
  expect(brief).toContain("## Sources");
  expect(brief).toContain("SYNTHETIC DEMO DATA");
});
