import { request } from "@playwright/test";

/** Tests create graphs named "E2E …" in the demo workspace. Remove them so the library stays clean. */
export default async function teardown() {
  const api = await request.newContext({ baseURL: process.env.BASE_URL ?? "http://localhost:3100" });
  try {
    const me = await (await api.post("/api/v1/auth/demo")).json();
    const graphs: { id: string; name: string }[] = await (await api.get("/api/v1/graphs")).json();
    for (const g of graphs.filter((x) => x.name.startsWith("E2E "))) {
      await api.delete(`/api/v1/graphs/${g.id}`, { headers: { "X-CSRF-Token": me.csrf_token } });
    }
  } catch {
    // best effort: a failed clean-up must not fail the run
  } finally {
    await api.dispose();
  }
}
