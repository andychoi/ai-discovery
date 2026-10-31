import { describe, expect, it } from "vitest";
import { AuthRequired, getJson, isAnswer, noteError, postJson } from "./api";

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

describe("writes", () => {
  it("posts JSON with the bearer token and returns the envelope", async () => {
    let seen: RequestInit | undefined;
    const fake = (async (_url: string, init?: RequestInit) => {
      seen = init;
      return new Response(JSON.stringify({ status: "ok", data: { note_id: 3 }, totals: {}, meta: {} }), { status: 200 });
    }) as unknown as typeof fetch;
    const ans = await postJson<{ note_id: number }>("/pages/x/notes", { body: "hi" }, "tok", fake);
    expect(ans.data.note_id).toBe(3);
    expect(seen?.method).toBe("POST");
    expect((seen?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("maps 401 to AuthRequired and surfaces server detail", async () => {
    const unauth = (async () => new Response("{}", { status: 401 })) as typeof fetch;
    await expect(postJson("/pages/x/notes", {}, null, unauth)).rejects.toBeInstanceOf(AuthRequired);
    const bad = (async () => new Response(JSON.stringify({ detail: "bad page id" }), { status: 422 })) as typeof fetch;
    await expect(postJson("/pages/x/notes", {}, null, bad)).rejects.toThrow("bad page id");
  });

  it("validates note bodies", () => {
    expect(noteError("   ")).toBe("Write something first.");
    expect(noteError("x".repeat(20001))).toContain("20,000");
    expect(noteError(" fine ")).toBeNull();
  });
});
