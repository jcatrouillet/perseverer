import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BodyMuscleMap } from "./BodyMuscleMap";

describe("BodyMuscleMap", () => {
  it("shades a worked muscle and leaves the others unworked", () => {
    const { container } = render(
      <BodyMuscleMap muscles={[{ muscle: "Chest", sets: 4, intensity: 1 }]} />,
    );
    expect(screen.getByRole("img", { name: /Muscles worked/ })).toBeInTheDocument();
    expect(container.querySelectorAll("title")).not.toHaveLength(0);
    const chest = [...container.querySelectorAll("g.body-map__muscle--worked")];
    expect(chest.length).toBe(1); // one chest group (both pectorals) on the front
    expect(chest[0]!.querySelector("title")!.textContent).toBe("Chest: 4 sets");
    expect(
      container.querySelector("g.body-map__muscle:not(.body-map__muscle--worked) title")!
        .textContent,
    ).toMatch(/not worked/);
  });
});
