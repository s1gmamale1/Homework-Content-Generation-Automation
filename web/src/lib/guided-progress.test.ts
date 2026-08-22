import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { GuidedProgress } from "../components/regeneration/guided-progress";

test("a historical campaign keeps Canary current without making wizard steps navigation", () => {
  const html = renderToStaticMarkup(
    createElement(GuidedProgress, {
      active: "canary",
      highestReachable: "canary",
      readOnly: true,
    }),
  );

  assert.equal((html.match(/ disabled=""/g) ?? []).length, 4);
  assert.match(
    html,
    /<button(?=[^>]*aria-current="step")(?=[^>]*disabled="")[^>]*>.*Canary/s,
  );
});
