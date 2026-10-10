import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";

const mocks = vi.hoisted(() => ({
  requestPasswordReset: vi.fn(),
  resetPassword: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    hasStoredCredential: () => false,
    requestPasswordReset: mocks.requestPasswordReset,
    resetPassword: mocks.resetPassword,
  };
});

beforeEach(() => {
  mocks.requestPasswordReset.mockReset();
  mocks.resetPassword.mockReset();
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

function renderGate() {
  return render(
    <AuthGate>
      <p>The app</p>
    </AuthGate>,
  );
}

describe("AuthGate forgotten password", () => {
  it("sends a reset link from the log-in page", async () => {
    mocks.requestPasswordReset.mockResolvedValue({ email_configured: true });
    renderGate();
    fireEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));
    fireEvent.change(screen.getByLabelText("Username or email"), {
      target: { value: " runner " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("If an account matches");
    expect(mocks.requestPasswordReset).toHaveBeenCalledWith("runner");

    fireEvent.click(screen.getByRole("button", { name: "Back to log in" }));
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("explains the alternative when the server can't send email", async () => {
    mocks.requestPasswordReset.mockResolvedValue({ email_configured: false });
    renderGate();
    fireEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));
    fireEvent.change(screen.getByLabelText("Username or email"), { target: { value: "runner" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("sync athlete set-password");
  });

  it("opens the new-password form from a reset link and hides the token", async () => {
    window.history.replaceState(null, "", "/reset-password?token=abc.def.ghi");
    mocks.resetPassword.mockResolvedValue(undefined);
    renderGate();
    expect(window.location.search).toBe("");

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "brand-new-pass" },
    });
    fireEvent.change(screen.getByLabelText("Confirm new password"), {
      target: { value: "different-pass" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    expect(screen.getByRole("alert")).toHaveTextContent("don't match");
    expect(mocks.resetPassword).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Confirm new password"), {
      target: { value: "brand-new-pass" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByRole("status")).toHaveTextContent("password has been changed");
    expect(mocks.resetPassword).toHaveBeenCalledWith("abc.def.ghi", "brand-new-pass");

    fireEvent.click(screen.getByRole("button", { name: "Go to log in" }));
    expect(window.location.pathname).toBe("/");
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });
});
