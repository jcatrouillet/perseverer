import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { clearCredential } from "../api/client";
import { LogoutButton } from "./LogoutButton";

vi.mock("../api/client", () => ({
  clearCredential: vi.fn(),
}));

describe("LogoutButton", () => {
  it("clears the query cache and the stored credential on click", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["some-key"], "cached-value");

    render(
      <QueryClientProvider client={queryClient}>
        <LogoutButton />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    // The previous athlete's cached data must not survive a fresh login.
    expect(queryClient.getQueryData(["some-key"])).toBeUndefined();
    expect(clearCredential).toHaveBeenCalledOnce();
  });
});
