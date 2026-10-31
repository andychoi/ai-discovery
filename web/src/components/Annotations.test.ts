import { describe, expect, it } from "vitest";
import { humanActions, type Annotation } from "./Annotations";

const base: Annotation = { id: 1, kind: "finding", category: null, text: "t", evidence: [], status: "open",
  signer_kind: "agent", client: "claude-code", created_at: "2026-10-31T00:00:00Z", updated_by: "agent" };

describe("annotation actions", () => {
  it("lets a person accept or reject a proposed decision", () => {
    expect(humanActions({ ...base, kind: "decision", status: "proposed" }).map((a) => a.status))
      .toEqual(["accepted", "rejected"]);
  });
  it("lets a person close open findings, and nothing else", () => {
    expect(humanActions(base).map((a) => a.status)).toEqual(["resolved", "false_positive"]);
    expect(humanActions({ ...base, status: "resolved" })).toEqual([]);
    expect(humanActions({ ...base, kind: "note", status: null })).toEqual([]);
    expect(humanActions({ ...base, kind: "decision", status: "accepted" })).toEqual([]);
  });
});
