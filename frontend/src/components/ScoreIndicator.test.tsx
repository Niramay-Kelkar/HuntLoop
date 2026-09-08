import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScoreIndicator } from "./ScoreIndicator";

/**
 * The gauge prints the real raw percent (score * 100), while the bar
 * fills to the score's fraction of SCORE_CEILING (0.6) so real scores,
 * which cluster ~0.03-0.59, still use most of the bar's range.
 */
describe("ScoreIndicator gauge", () => {
  it("prints the raw percent, not the calibrated fill", () => {
    render(<ScoreIndicator score={0.3} />);
    // 0.3 -> 30% printed; the bar would be 50% filled (0.3 / 0.6).
    expect(screen.getByText("30")).toBeInTheDocument();
  });

  it("caps the printed value from a score above the ceiling at its real percent", () => {
    render(<ScoreIndicator score={0.68} />);
    expect(screen.getByText("68")).toBeInTheDocument();
  });

  it("shows a dash instead of a number when the job is not scored", () => {
    render(<ScoreIndicator score={null} />);
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("labels the large gauge and still shows a dash when unscored", () => {
    render(<ScoreIndicator score={null} size="lg" />);
    expect(screen.getByText("not scored")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("captions the large gauge with the raw percent when scored", () => {
    render(<ScoreIndicator score={0.52} size="lg" />);
    expect(screen.getByText("52")).toBeInTheDocument();
    expect(screen.getByText("match score")).toBeInTheDocument();
  });
});
