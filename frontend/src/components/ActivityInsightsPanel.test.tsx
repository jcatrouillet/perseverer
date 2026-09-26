import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { InsightOut } from "../api/types";
import { ActivityInsightsPanel } from "./ActivityInsightsPanel";

const boulderingRecord: InsightOut = {
  kind: "climb_record",
  window: "all_time",
  title: "Highest attempted grade ever",
  detail: {},
  value_num: 7,
  metric_key: null,
  sport_family: "climb",
  activity_id: "boulder-1",
  local_date: "2026-06-15",
  computed_at: "2026-06-15T10:00:00Z",
};

describe("ActivityInsightsPanel", () => {
  it("uses the bouldering heading when requested", () => {
    render(<ActivityInsightsPanel insights={[boulderingRecord]} heading="Bouldering insights" />);

    expect(screen.getByRole("heading", { name: "Bouldering insights" })).toBeInTheDocument();
    expect(screen.getByText("Highest attempted grade ever")).toBeInTheDocument();
  });
});
