import { afterEach, describe, expect, it } from "vitest";
import { getJson, postJson, type Answer } from "./api";
import { currentLocation, docHref, searchHref } from "./router";
import { searchStatic, staticAnswer, type StaticData } from "./staticData";

const meta = { scan_id: 1, degraded: [], contract_version: "1" };
const ok = <T,>(data: T): Answer<T> => ({ status: "ok", data, totals: {}, truncated: false, omitted: [], meta });

const DATA: StaticData = {
  version: 1,
  answers: {
    "/status": ok({ project: { slug: "shop" } }),
    "/pages/table%3Aorders": ok({ page_id: "table:orders", title: "orders" }),
  },
  search: [
    { page_id: "table:orders", title: "orders", type: "table", text: "columns id status total" },
    { page_id: "flow:x.OrderController.create", title: "Flow: create", type: "flow", text: "writes orders table" },
    { page_id: "module:src", title: "src", type: "module", text: "nothing relevant" },
  ],
};

function withWindow(hash = ""): void {
  (globalThis as unknown as { window: unknown }).window = { __DISCOVERY_DATA__: DATA, location: { hash } };
}

afterEach(() => { delete (globalThis as unknown as { window?: unknown }).window; });

describe("static export data", () => {
  it("answers recorded paths and not_found otherwise", () => {
    expect(staticAnswer(DATA, "/pages/table%3Aorders").status).toBe("ok");
    expect(staticAnswer(DATA, "/pages/table%3Anope").status).toBe("not_found");
    expect(staticAnswer(DATA, "/source?path=a.java&start=1").status).toBe("unknown");
    expect(staticAnswer(DATA, "/graph/neighborhood?node=a&depth=1").status).toBe("unknown");
    expect(staticAnswer(DATA, "/impact?target=a").status).toBe("unknown");
  });

  it("searches client-side: all tokens must match, title hits first", () => {
    const hits = (searchStatic(DATA, "orders", 10).data.hits as { page_id: string }[]).map((h) => h.page_id);
    expect(hits).toEqual(["table:orders", "flow:x.OrderController.create"]);
    expect(searchStatic(DATA, "orders zebra", 10).data.hits).toEqual([]);
    expect(searchStatic(DATA, "orders", 1).truncated).toBe(true);
  });

  it("getJson reads the inlined data without fetching; writes are refused", async () => {
    withWindow();
    const never = (async () => { throw new Error("fetch called"); }) as typeof fetch;
    const status = await getJson<{ project: { slug: string } }>("/status", never);
    expect(status.data.project.slug).toBe("shop");
    const found = await getJson<{ hits: unknown[] }>("/search?q=orders&limit=20", never);
    expect(found.data.hits.length).toBe(2);
    await expect(postJson("/pages/x/notes", { body: "hi" }, null, never)).rejects.toThrow("read-only static export");
  });

  it("routes through the hash in a static export", () => {
    withWindow("#/docs/table%3Aorders");
    expect(docHref("table:orders")).toBe("#/docs/table%3Aorders");
    expect(docHref("overview:system")).toBe("#/");
    expect(searchHref("a b")).toBe("#/search?q=a%20b");
    expect(currentLocation()).toEqual(["/docs/table%3Aorders", ""]);
    withWindow("#/search?q=orders");
    expect(currentLocation()).toEqual(["/search", "?q=orders"]);
    withWindow("");
    expect(currentLocation()).toEqual(["/", ""]);
  });
});
