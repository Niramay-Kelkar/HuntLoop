import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProvisionalScoreNote, ScoreIndicator } from "./ScoreIndicator";

/**
 * `score` is the composite match score from the API - already a
 * calibrated value in [0, 1] (calibration moved server-side with
 * composite-match-score-v1), so the ring prints score * 100 directly
 * and the arc sweeps to the same fraction.
 */
describe("ScoreIndicator ring", () => {
  it("prints the score as a percent", () => {
    render(<ScoreIndicator score={0.3} />);
    expect(screen.getByText("30")).toBeInTheDocument();
  });

  it("clamps a score above 1 to 100 percent", () => {
    render(<ScoreIndicator score={1.2} />);
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("shows a dash instead of a number when the job is not scored", () => {
    render(<ScoreIndicator score={null} />);
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("labels the large ring and still shows a dash when unscored", () => {
    render(<ScoreIndicator score={null} size="lg" />);
    expect(screen.getByText("not scored")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("captions the large ring with the score percent when scored", () => {
    render(<ScoreIndicator score={0.52} size="lg" />);
    expect(screen.getByText("52")).toBeInTheDocument();
    expect(screen.getByText("match score")).toBeInTheDocument();
  });
});

describe("ScoreIndicator provisional marker", () => {
  it("shows no marker for a full-basis score", () => {
    render(<ScoreIndicator score={0.5} />);
    expect(screen.queryByLabelText(/provisional/i)).not.toBeInTheDocument();
  });

  it("shows a compact marker on the small ring for a provisional score", () => {
    render(<ScoreIndicator score={0.5} provisional />);
    expect(screen.getByLabelText(/skills analysis pending/i)).toBeInTheDocument();
  });

  it("spells the marker out under the large ring for a provisional score", () => {
    render(<ScoreIndicator score={0.5} size="lg" provisional />);
    expect(screen.getByText(/score provisional . skills analysis pending/i)).toBeInTheDocument();
  });
});

describe("ProvisionalScoreNote legend", () => {
  it("renders the legend when at least one visible posting is provisional", () => {
    render(
      <ProvisionalScoreNote
        jobs={[{ score_basis: "full" }, { score_basis: "partial" }]}
      />,
    );
    expect(screen.getByText(/score provisional/i)).toBeInTheDocument();
  });

  it("renders nothing when no visible posting is provisional", () => {
    const { container } = render(
      <ProvisionalScoreNote jobs={[{ score_basis: "full" }, { score_basis: null }]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
