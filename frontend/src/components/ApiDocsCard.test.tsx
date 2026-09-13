import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiDocsCard } from "./ApiDocsCard";

describe("ApiDocsCard", () => {
  it("links to the static API documentation page in a new tab", () => {
    render(<ApiDocsCard />);
    const link = screen.getByRole("link", { name: "Open API documentation" });
    expect(link).toHaveAttribute("href", "/api-docs.html");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
