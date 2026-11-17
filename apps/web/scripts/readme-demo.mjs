// Records the README demo GIF from the running demo (synthetic data, dark theme).
// Usage: node scripts/readme-demo.mjs   (needs ffmpeg on PATH)
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = process.env.BASE_URL ?? "http://localhost:3100";
const out = process.env.OUT ?? "../../docs/screenshots/demo.gif";
const size = { width: Number(process.env.W ?? 1280), height: Number(process.env.H ?? 800) };
const mp4Dir = process.env.MP4_DIR;               // W=1280 H=720 MP4_DIR=… writes 16:9 segments for a video edit
const dir = mkdtempSync(join(tmpdir(), "openkg-demo-"));

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: size, colorScheme: "dark", recordVideo: { dir, size } });
await ctx.addInitScript(() => {
  try { localStorage.setItem("openkg-theme", "dark"); } catch {}
  // Recorded video has no pointer, so draw one.
  addEventListener("DOMContentLoaded", () => {
    const dot = document.createElement("div");
    dot.style.cssText = "position:fixed;z-index:2147483647;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;" +
      "background:rgba(255,255,255,.85);box-shadow:0 0 0 2px rgba(0,0,0,.45);pointer-events:none;left:-40px;top:-40px;" +
      "transition:transform .12s";
    document.body.appendChild(dot);
    addEventListener("mousemove", (e) => { dot.style.left = e.clientX + "px"; dot.style.top = e.clientY + "px"; }, true);
    addEventListener("mousedown", () => { dot.style.transform = "scale(.6)"; }, true);
    addEventListener("mouseup", () => { dot.style.transform = ""; }, true);
  });
});
const page = await ctx.newPage();
const t0 = Date.now();
const now = () => (Date.now() - t0) / 1000;
const keep = [];                       // [start, end] seconds of video that make it into the GIF
let open = null;
const cut = () => { if (open !== null) { keep.push([open, now()]); open = null; } };
const roll = () => { open = now(); };
const pause = (ms) => page.waitForTimeout(ms);
async function click(locator) {
  await locator.scrollIntoViewIfNeeded();
  const b = await locator.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 18 });
  await pause(250);
  await locator.click();
}
const section = (name) => page.getByRole("navigation", { name: "Graph sections" }).getByRole("link", { name });

await page.goto(base + "/login");
await page.getByRole("button", { name: /Enter demo/ }).click();
await page.waitForURL(base + "/");
await page.goto(base + "/new");
await page.getByRole("button", { name: /Series B product leaders/ }).waitFor();
await pause(1200);

roll();                                // 1. ask, start, and watch the graph grow
await pause(700);
await click(page.getByRole("button", { name: /Series B product leaders/ }));
await page.getByRole("heading", { name: "Charter", exact: true }).waitFor();
await pause(1600);
await click(page.getByRole("button", { name: /Create graph/ }));
await page.getByRole("heading", { name: "Nothing mapped yet" }).waitFor();
await pause(700);
await click(page.getByRole("button", { name: /Start research|Run/ }).first());
await pause(800);
await click(page.getByRole("button", { name: "Start simulation" }));
await page.mouse.move(size.width / 2, size.height - 40, { steps: 18 });
await pause(Number(process.env.GROW_MS ?? 14000));
await click(section("Research"));      // 2. what the agents are doing
await pause(3500);
cut();

await page.waitForFunction(() => /Awaiting review|Completed/.test(document.querySelector("header")?.parentElement?.innerText ?? "")
  && !document.body.innerText.includes("Working"), null, { timeout: 120000 });
await section("Graph").click();
await page.waitForSelector(".react-flow__node");
await pause(1200);

roll();                                // 3. a fact and the quote behind it
await pause(600);
await click(page.locator('.react-flow__node[aria-label="Company Quillon Data"]'));
await pause(3500);
cut();

await page.getByRole("link", { name: /^Review/ }).click();
await page.getByRole("tab", { name: "By type" }).click();
await pause(600);
roll();                                // 4. review a conflict
await click(page.getByRole("button", { name: /Conflict on Quillon Data/ }));
await pause(3000);
await click(section("Table"));         // 5. the table
await pause(600);
await click(page.getByRole("tab", { name: "Company" }));
await pause(3000);
cut();

// Remove the graph this recording created (and any left by an interrupted earlier take).
const csrf = (await (await page.request.get(`${base}/api/v1/auth/me`)).json()).csrf_token;
const graphs = await (await page.request.get(`${base}/api/v1/graphs`)).json();
const mine = (graphs.items ?? graphs).filter((g) => g.name === "Series B product leaders")
  .sort((a, b) => a.created_at.localeCompare(b.created_at)).slice(1);   // the oldest is the seeded demo graph
for (const g of mine) await page.request.delete(`${base}/api/v1/graphs/${g.id}`, { headers: { "X-CSRF-Token": csrf } });
await ctx.close();
await browser.close();

const video = join(dir, readdirSync(dir).find((f) => f.endsWith(".webm")));
if (mp4Dir) {
  keep.forEach(([a, b], i) => execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", a.toFixed(2), "-to", b.toFixed(2), "-i", video,
    "-an", "-c:v", "libx264", "-crf", "16", "-vf", "scale=1920:-2:flags=lanczos", "-pix_fmt", "yuv420p", "-r", "30", join(mp4Dir, `seg${i + 1}.mp4`)]));
  console.log(`segments written to ${mp4Dir}`);
  process.exit(0);
}
const parts = keep.map(([a, b], i) => `[0:v]trim=${a.toFixed(2)}:${b.toFixed(2)},setpts=PTS-STARTPTS[v${i}]`).join(";");
const filter = `${parts};${keep.map((_, i) => `[v${i}]`).join("")}concat=n=${keep.length},setpts=PTS/1.5,fps=8,scale=900:-1:flags=lanczos,` +
  "split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle";
execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", video, "-filter_complex", filter, out]);
console.log(`demo written to ${out} (${keep.map(([a, b]) => (b - a).toFixed(1)).join(" + ")} s)`);
