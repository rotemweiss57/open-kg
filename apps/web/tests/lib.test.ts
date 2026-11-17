import { describe, expect, it } from "vitest";
import { cadence, firstLine, fmtValid, validRange } from "../lib/format";
import { layoutGraph } from "../lib/layout";

describe("valid-time formatting", () => {
  it("handles year, month and day precision", () => {
    expect(fmtValid("2026")).toBe("2026");
    expect(fmtValid("2026-03")).toBe("Mar 2026");
    expect(fmtValid("2026-03-19")).toBe("Mar 19, 2026");
    expect(validRange("2020", "2024")).toBe("2020 – 2024");
    expect(validRange("2025-01", null)).toBe("since Jan 2025");
    expect(validRange(null, null)).toBe("");
  });
  it("describes monitor cadence", () => {
    expect(cadence(1440)).toBe("Daily");
    expect(cadence(60)).toBe("Hourly");
    expect(cadence(4320)).toBe("Every 3 days");
  });
});

describe("graph layout", () => {
  const nodes = Array.from({ length: 12 }, (_, i) => ({ id: `n${i}`, degree: i % 4 }));
  const edges = nodes.slice(1).map((n, i) => ({ from_id: "n0", to_id: n.id, i }));

  it("is deterministic", () => {
    expect([...layoutGraph(nodes, edges, new Map())]).toEqual([...layoutGraph(nodes, edges, new Map())]);
  });
  it("does not move anything when nothing is new", () => {
    const first = layoutGraph(nodes, edges, new Map());
    expect([...layoutGraph(nodes, edges, first)]).toEqual([...first]);
  });
  it("keeps the existing map stable when discoveries are added and places them nearby", () => {
    const first = layoutGraph(nodes, edges, new Map());
    const grown = layoutGraph([...nodes, { id: "new", degree: 1 }], [...edges, { from_id: "n3", to_id: "new" }], first);
    for (const n of nodes) {
      const a = first.get(n.id)!, b = grown.get(n.id)!;
      expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeLessThan(90);
    }
    const anchor = grown.get("n3")!, fresh = grown.get("new")!;
    expect(Math.hypot(anchor.x - fresh.x, anchor.y - fresh.y)).toBeLessThan(320);
  });
});

describe("headings from agent assignments", () => {
  it("keeps the first sentence and drops generic prefixes", () => {
    expect(firstLine("Investigation task: Investigate Diamond Foundry. Find its investors.")).toBe("Investigate Diamond Foundry");
    expect(firstLine("Search for Notion's leadership team and founders. Find: 1. Current CEO")).toBe("Search for Notion's leadership team and founders");
    expect(firstLine("Find investors\nthen founders")).toBe("Find investors");
    expect(firstLine("x".repeat(200)).length).toBe(96);
    expect(firstLine(null)).toBe("");
  });
});
