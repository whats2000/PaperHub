import { describe, expect, it } from "vitest";

import { splitProposals } from "@/lib/proposals";

describe("splitProposals", () => {
  it("strips proposal lines and returns them in order", () => {
    const r = splitProposals(
      "The answer [chunk:3].\n\n<proposal>Compare with paper 2</proposal>\n<proposal> Make slides </proposal>",
    );
    expect(r.body).toBe("The answer [chunk:3].");
    expect(r.proposals).toEqual(["Compare with paper 2", "Make slides"]);
  });

  it("leaves an answer without proposals untouched", () => {
    expect(splitProposals("Just text <b>x</b>")).toEqual({
      body: "Just text <b>x</b>",
      proposals: [],
    });
  });

  it("hides an unfinished proposal while streaming, without a button", () => {
    expect(splitProposals("Answer.\n<proposal>Half writ")).toEqual({
      body: "Answer.",
      proposals: [],
    });
    expect(splitProposals("Answer.\n<propo").body).toBe("Answer.");
  });
});
