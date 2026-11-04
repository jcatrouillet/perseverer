import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiGet, getActingAs, setActingAs, storeJwt } from "./client";

const fetchMock = vi.fn();

beforeEach(() => {
  localStorage.clear();
  window.__PERSEVERER_CONFIG__ = { apiBaseUrl: "" };
  vi.stubGlobal("fetch", fetchMock);
  storeJwt("token");
  fetchMock.mockReset();
});
afterEach(() => vi.unstubAllGlobals());

const ok = () => new Response("{}", { status: 200 });

describe("acting as an athlete", () => {
  it("sends X-Act-As on every request except the coach endpoints", async () => {
    setActingAs({ id: "a1", display_name: "Alex" });
    fetchMock.mockImplementation(async () => ok());
    await apiGet("/api/v1/activities");
    await apiGet("/api/v1/coach");
    const headers = (i: number) => new Headers(fetchMock.mock.calls[i]![1].headers);
    expect(headers(0).get("X-Act-As")).toBe("a1");
    expect(headers(1).get("X-Act-As")).toBeNull();
  });

  it("sends nothing when not acting", async () => {
    fetchMock.mockImplementation(async () => ok());
    await apiGet("/api/v1/activities");
    expect(new Headers(fetchMock.mock.calls[0]![1].headers).get("X-Act-As")).toBeNull();
  });

  it("falls back to the coach's own view when the athlete stops sharing", async () => {
    setActingAs({ id: "a1", display_name: "Alex" });
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify({ detail: "you are not this athlete's coach" }), {
          status: 403,
        }),
    );
    await expect(apiGet("/api/v1/activities")).rejects.toThrow();
    expect(getActingAs()).toBeNull();
  });
});
