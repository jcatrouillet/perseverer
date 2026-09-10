import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EmailReportConfigOut } from "../api/types";
import { EmailReportsCard } from "./EmailReportsCard";

const mockSetMutate = vi.fn();
const mockTestMutate = vi.fn();

let configState: { data?: EmailReportConfigOut; isLoading: boolean; isSuccess: boolean } = {
  data: undefined,
  isLoading: false,
  isSuccess: false,
};

vi.mock("../api/queries", () => ({
  useEmailReportConfig: () => configState,
  useSetEmailReportConfig: () => ({
    mutate: mockSetMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
  useSendTestEmailReport: () => ({
    mutate: mockTestMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
}));

function config(over: Partial<EmailReportConfigOut> = {}): EmailReportConfigOut {
  return {
    weekly_enabled: false,
    monthly_enabled: false,
    smtp_configured: true,
    recipient_email: "jerome@example.com",
    ...over,
  };
}

beforeEach(() => {
  configState = { data: config(), isLoading: false, isSuccess: true };
  mockSetMutate.mockClear();
  mockTestMutate.mockClear();
});

describe("EmailReportsCard", () => {
  it("reflects the stored config in the checkboxes", () => {
    configState = {
      data: config({ weekly_enabled: true }),
      isLoading: false,
      isSuccess: true,
    };
    render(<EmailReportsCard />);
    expect(screen.getByLabelText(/Weekly summary/)).toBeChecked();
    expect(screen.getByLabelText(/Monthly summary/)).not.toBeChecked();
  });

  it("saves the toggled values", () => {
    render(<EmailReportsCard />);
    fireEvent.click(screen.getByLabelText(/Weekly summary/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockSetMutate).toHaveBeenCalledWith({
      weekly_enabled: true,
      monthly_enabled: false,
    });
  });

  it("disables the controls and explains when SMTP is not configured", () => {
    configState = {
      data: config({ smtp_configured: false }),
      isLoading: false,
      isSuccess: true,
    };
    render(<EmailReportsCard />);
    expect(screen.getByLabelText(/Weekly summary/)).toBeDisabled();
    expect(screen.getByText(/isn't configured on this server/)).toBeInTheDocument();
  });

  it("prompts to set an email when the Profile email is missing", () => {
    configState = {
      data: config({ recipient_email: null }),
      isLoading: false,
      isSuccess: true,
    };
    render(<EmailReportsCard />);
    expect(screen.getByText(/add one on the Profile tab/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send test email" })).toBeDisabled();
  });

  it("sends a test email on click", () => {
    render(<EmailReportsCard />);
    fireEvent.click(screen.getByRole("button", { name: "Send test email" }));
    expect(mockTestMutate).toHaveBeenCalled();
  });
});
