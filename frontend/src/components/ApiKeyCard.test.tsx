import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiKeyCard } from "./ApiKeyCard";

const mockCreateMutate = vi.fn();
const mockRevokeMutate = vi.fn();
let statusState: { data?: { enabled: boolean; created_at: string | null }; isLoading: boolean } = {
  data: { enabled: false, created_at: null },
  isLoading: false,
};
let createState: { isPending: boolean; isError: boolean; data?: { api_key: string } } = {
  isPending: false,
  isError: false,
};
let revokeState: { isPending: boolean } = { isPending: false };

vi.mock("../api/queries", () => ({
  useApiKeyStatus: () => statusState,
  useCreateApiKey: () => ({ ...createState, mutate: mockCreateMutate }),
  useDeleteApiKey: () => ({ ...revokeState, mutate: mockRevokeMutate }),
}));

beforeEach(() => {
  statusState = { data: { enabled: false, created_at: null }, isLoading: false };
  createState = { isPending: false, isError: false };
  revokeState = { isPending: false };
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

describe("ApiKeyCard", () => {
  it("shows 'Generate API key' and no Revoke button when disabled", () => {
    render(<ApiKeyCard />);
    expect(screen.getByRole("button", { name: "Generate API key" })).toBeInTheDocument();
    expect(screen.queryByText("Revoke key")).not.toBeInTheDocument();
  });

  it("shows 'Rotate key' and 'Revoke key' when already enabled", () => {
    statusState = { data: { enabled: true, created_at: "2026-09-01T00:00:00Z" }, isLoading: false };
    render(<ApiKeyCard />);
    expect(screen.getByText("Rotate key")).toBeInTheDocument();
    expect(screen.getByText("Revoke key")).toBeInTheDocument();
  });

  it("calls the revoke mutation when Revoke key is clicked", () => {
    statusState = { data: { enabled: true, created_at: "2026-09-01T00:00:00Z" }, isLoading: false };
    render(<ApiKeyCard />);
    fireEvent.click(screen.getByText("Revoke key"));
    expect(mockRevokeMutate).toHaveBeenCalled();
  });

  it("shows an error message when generation fails", () => {
    createState = { isPending: false, isError: true };
    render(<ApiKeyCard />);
    expect(screen.getByText("Could not generate an API key.")).toBeInTheDocument();
  });

  it("generates a key, masks it by default, and reveals it on Show", () => {
    createState = { isPending: false, isError: false, data: { api_key: "abc123secretkey" } };
    render(<ApiKeyCard />);
    fireEvent.click(screen.getByRole("button", { name: "Generate API key" }));

    expect(mockCreateMutate).toHaveBeenCalled();
    const input = screen.getByDisplayValue("abc123secretkey") as HTMLInputElement;
    expect(input.type).toBe("password");

    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(input.type).toBe("text");

    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(input.type).toBe("password");
  });

  it("shows a Copy button for the freshly-generated key", () => {
    createState = { isPending: false, isError: false, data: { api_key: "abc123secretkey" } };
    render(<ApiKeyCard />);
    expect(screen.getByText("Copy")).toBeInTheDocument();
  });
});
