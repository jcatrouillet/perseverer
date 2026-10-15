import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BulkImportHelp } from "./BulkImportHelp";

describe("BulkImportHelp", () => {
  it("opens the Garmin steps in a popup, with a link to Garmin's export page", () => {
    render(<BulkImportHelp source="garmin" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "How do I get this file?" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Request Data Export")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Account Data Management" })).toHaveAttribute(
      "href",
      "https://www.garmin.com/account/datamanagement/",
    );
  });

  it("shows Strava's own steps for a Strava export", () => {
    render(<BulkImportHelp source="strava" />);
    fireEvent.click(screen.getByRole("button", { name: "How do I get this file?" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Request download")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "My Account" })).toHaveAttribute(
      "target",
      "_blank",
    );
  });
});
