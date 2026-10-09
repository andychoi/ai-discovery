import { describe, expect, it } from "vitest";
import { getJson, isAnswer } from "./api";

describe("api envelope", () => {
  it("recognises the Answer envelope", () => {
    expect(isAnswer({ status: "ok", data: {}, totals: {}, meta: {} })).toBe(true);
    expect(isAnswer({ data: {} })).toBe(false);
  });

  it("rejects non-envelope bodies", async () => {
    const fake = (async () => new Response(JSON.stringify({ hello: 1 }), { status: 200 })) as typeof fetch;
    await expect(getJson("/status", fake)).rejects.toThrow("unexpected response shape");
  });
});
