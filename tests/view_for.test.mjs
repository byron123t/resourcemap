// The view picker (VIEW_FOR / viewFor in site/index.html): node --test 'tests/*.test.mjs'
// tests/view_for_cases.json is shared with tests/test_view_for.py, which checks pipeline/live/view_for.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pageConst as C, viewFor } from "../cli/lib/page.mjs";

const CASES = JSON.parse(readFileSync(new URL("view_for_cases.json", import.meta.url), "utf8"));

test("every rule names a view that exists", () => {
  const V = C("VIEWS");
  C("VIEW_FOR").forEach(r => assert.ok(V[r.view], `no view "${r.view}"`));
  assert.ok(!C("VIEW_FOR").at(-1).src && !C("VIEW_FOR").at(-1).text, "last rule is a catch-all");
});

test("stories and scenarios get the view that shows them", () => {
  for (const { ctx, view } of CASES) assert.equal(viewFor(ctx), view, JSON.stringify(ctx));
});
