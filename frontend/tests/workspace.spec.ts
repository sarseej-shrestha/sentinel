import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir } from "node:fs/promises";

const workflows = [
  ["Late deliveries", "Supplier late deliveries"],
  ["Inventory coverage", "Inventory coverage"],
  ["Demand forecast", "Demand forecast"],
  ["Supplier investigation", "Supplier investigation"],
  ["What-if simulation", "Demand-increase simulation"],
];
for (const [nav, title] of workflows) {
  test(`${nav}: real analysis, evidence, edit, approval and replay`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/");
    await page.getByRole("button", { name: nav, exact: false }).first().click();
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Run analysis", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Inspect source evidence" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Inspect source evidence" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Inspect source evidence",
    });
    await expect(dialog.getByText("Snapshot integrity verified")).toBeVisible();
    if (nav === "Demand forecast") {
      await dialog.getByRole("button", { name: "Next", exact: true }).click();
      await expect(dialog.getByText("26–50 of 180")).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await page.getByLabel("Recommendation", { exact: true }).selectOption("r1");
    await page.getByLabel("Reviewer name").fill("Browser test reviewer");
    await page
      .getByLabel("Review note")
      .fill("Inspect source records before a simulated follow-up.");
    await page.getByRole("button", { name: "Save pending draft" }).click();
    await expect(page.getByText("Pending · revision 1")).toBeVisible();
    await page.getByLabel("Review note").fill("Revised review note.");
    await page.getByRole("button", { name: "Revert draft" }).click();
    await page
      .getByRole("button", { name: "Discard unsaved edits", exact: true })
      .click();
    await expect(page.getByLabel("Review note")).toHaveValue(
      "Inspect source records before a simulated follow-up.",
    );
    await page.getByLabel("Review note").fill("Revised review note.");
    await page.getByRole("button", { name: "Save edits" }).click();
    await expect(page.getByText("Pending · revision 2")).toBeVisible();
    await page
      .getByRole("button", { name: "Approve simulation", exact: true })
      .click();
    await page.getByRole("button", { name: "Confirm approval" }).click();
    await expect(
      page.getByText(
        "Simulation approved · revision 3. This decision is final.",
      ),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Decision history", exact: true })
      .click();
    await expect(page.getByText(/audit events verified/)).toBeVisible();
    await page.getByRole("button", { name: "Open review" }).first().click();
    await expect(page.getByText(/Saved snapshot/)).toBeVisible();
    await expect(page.getByLabel("Review note")).toBeDisabled();
    expect(errors).toEqual([]);
  });
}

test("accessible keyboard flow, table alternatives and responsive layout", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Local workspace connected")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to workspace" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Inspect source evidence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Analysis result", exact: true }),
  ).toBeFocused();
  await page.getByText("View forecast data table").click();
  await expect(page.getByRole("table").first()).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Inspect source evidence" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Inspect source evidence" }),
  ).toBeFocused();
  await mkdir("../artifacts/browser", { recursive: true });
  await page.screenshot({
    path: "../artifacts/browser/forecast.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: "../artifacts/browser/mobile.png",
    fullPage: true,
  });
});

test("empty results, injected service failure, retry and preserved inputs", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Inventory coverage", exact: false })
    .click();
  await page.getByLabel("Coverage threshold (days)").fill("1");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "No matching observations" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save pending draft" }),
  ).toHaveCount(0);
  await page.getByLabel("Coverage threshold (days)").fill("14");
  await page.route(
    "**/api/analyses",
    (r) =>
      r.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Injected unavailable service" }),
      }),
    { times: 1 },
  );
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Injected unavailable service",
  );
  await expect(page.getByLabel("Coverage threshold (days)")).toHaveValue("14");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Inspect source evidence" }),
  ).toBeVisible();
});

test("late responses cannot replace a newer task; loading is announced", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Local workspace connected")).toBeVisible();
  await page.route(
    "**/api/analyses",
    async (r) => {
      await new Promise((resolve) => setTimeout(resolve, 900));
      await r.continue().catch(() => {});
    },
    { times: 1 },
  );
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByText(
      "Checking the request, running read-only analysis and saving evidence…",
    ),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "What-if simulation", exact: false })
    .click();
  await page.waitForTimeout(1500);
  await expect(
    page.getByRole("heading", { name: "Demand-increase simulation" }),
  ).toBeVisible();
  await expect(
    page.getByText("A clear path from question to decision"),
  ).toBeVisible();
});

for (const [nav] of workflows) {
  test(`${nav}: initial, loading, controlled uncertainty and error recovery`, async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: nav, exact: false }).first().click();
    await expect(
      page.getByText("A clear path from question to decision"),
    ).toBeVisible();
    // Inject response states to exercise recovery UI; these are not model outcomes.
    await page.route(
      "**/api/analyses",
      async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 300));
        const response = await route.fetch();
        const body = await response.json();
        await route.fulfill({
          response,
          json: {
            ...body,
            state: "uncertain",
            message: "Injected incomplete evidence",
            recommendations: [],
          },
        });
      },
      { times: 1 },
    );
    await page
      .getByRole("button", { name: "Run analysis", exact: true })
      .click();
    await expect(
      page.getByText(
        "Checking the request, running read-only analysis and saving evidence…",
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Review the available information" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Save pending draft" }),
    ).toHaveCount(0);
    await page.route(
      "**/api/analyses",
      (route) =>
        route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Injected storage failure" }),
        }),
      { times: 1 },
    );
    await page
      .getByRole("button", { name: "Run analysis", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText(
      "Injected storage failure",
    );
    await page
      .getByRole("button", { name: "Run analysis", exact: true })
      .click();
    await expect(
      page.getByLabel("Recommendation", { exact: true }),
    ).toBeVisible();
  });
}

test("failed save preserves a draft, retry succeeds and rejection is final", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByLabel("Recommendation", { exact: true }).selectOption("r1");
  await page.getByLabel("Reviewer name").fill("Browser recovery reviewer");
  await page
    .getByLabel("Review note")
    .fill("Preserve this note after a failed save.");
  await page.route(
    "**/api/proposals",
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Injected failed save" }),
      }),
    { times: 1 },
  );
  await page.getByRole("button", { name: "Save pending draft" }).click();
  await expect(page.getByRole("alert")).toContainText("Injected failed save");
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Preserve this note after a failed save.",
  );
  await page.getByRole("button", { name: "Save pending draft" }).click();
  await expect(page.getByText("Pending · revision 1")).toBeVisible();
  await page
    .getByRole("button", { name: "Reject simulation", exact: true })
    .click();
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(page.getByLabel("Review note")).toBeDisabled();
  await expect(page.getByText(/This decision is final/)).toContainText(
    "rejected",
  );
});

test("unsaved review survives task, history and reload navigation", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByLabel("Recommendation", { exact: true }).selectOption("r1");
  await page.getByLabel("Reviewer name").fill("Recovery reviewer");
  await page
    .getByLabel("Review note")
    .fill("Keep the unsaved investigation rationale");
  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page
    .getByRole("button", { name: "Late deliveries", exact: false })
    .click();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Keep the unsaved investigation rationale",
  );
  await page.getByRole("button", { name: "Save pending draft" }).click();
  await expect(page.getByText("Pending · revision 1")).toBeVisible();
  await page.getByLabel("Review note").fill("Unsaved revision two");
  await page
    .getByRole("button", { name: "Decision history", exact: true })
    .click();
  await page.getByRole("button", { name: "Open review" }).first().click();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Unsaved revision two",
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Decision history", exact: true })
    .click();
  await page.getByRole("button", { name: "Open review" }).first().click();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Unsaved revision two",
  );
  await page.getByRole("button", { name: "Revert draft" }).click();
  await expect(
    page.getByRole("dialog", { name: "Discard unsaved edits?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Unsaved revision two",
  );
});

test("switching recommendations asks before replacing an unsaved note", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  const recommendation = page.getByLabel("Recommendation", { exact: true });
  await recommendation.selectOption("r1");
  await page
    .getByLabel("Review note")
    .fill("My unfinished Supplier A follow-up");
  await recommendation.selectOption("r2");
  await expect(
    page.getByRole("dialog", { name: "Discard unsaved note?" }),
  ).toBeVisible();
  await expect(recommendation).toHaveValue("r1");
  await page.getByRole("button", { name: "Keep note" }).click();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "My unfinished Supplier A follow-up",
  );
  await recommendation.selectOption("r2");
  await page.getByRole("button", { name: "Discard note and switch" }).click();
  await expect(recommendation).toHaveValue("r2");
  await expect(page.getByLabel("Review note")).not.toHaveValue(
    "My unfinished Supplier A follow-up",
  );
});

test("coverage and demand sensitivity expose observation dates and assumptions", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Inventory coverage", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  const coverage = page.getByRole("region", {
    name: "Inventory coverage results",
  });
  await expect(
    coverage.getByRole("columnheader", { name: "Inventory date" }),
  ).toBeVisible();
  await expect(
    coverage.getByRole("columnheader", { name: "History (days)" }),
  ).toBeVisible();
  await expect(page.getByText(/28 observed daily demand values/)).toBeVisible();
  await page
    .getByRole("button", { name: "What-if simulation", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  const scenario = page.getByRole("region", {
    name: "Demand-increase simulation results",
  });
  await expect(
    scenario.getByRole("columnheader", { name: "Inventory date" }),
  ).toBeVisible();
  await expect(
    scenario.getByRole("columnheader", { name: "History (days)" }),
  ).toBeVisible();
  await expect(
    page.getByText(/replenishment, allocations, lead times and capacity/i),
  ).toBeVisible();
});

test("supplier transaction investigation reconciles the saved target", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByRole("button", { name: "Inspect source evidence" }).click();
  await expect(
    page.getByText(
      "19 late / 31 evaluable · 0 excluded · 0 unassignable dates",
    ),
  ).toBeVisible();
  const region = page.getByRole("region", {
    name: "Captured transaction records",
    exact: true,
  });
  await expect(region.locator("tbody tr")).toHaveCount(10);
  await expect(region).toContainText("promised date");
  await page.getByRole("button", { name: "Next source page" }).click();
  await expect(page.getByText("Source records 11–20 of 31")).toBeVisible();
  await page.getByLabel("Evidence target").selectOption("supplier:S2");
  await expect(
    page.getByText("7 late / 31 evaluable · 0 excluded · 0 unassignable dates"),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Inspect source evidence" }),
  ).toBeFocused();
});

for (const [fixture, nav, expected] of [
  [
    "missing-demand",
    "Inventory coverage",
    "17 assessed · 17 matching · 0 assessed outside threshold · 1 unevaluable",
  ],
  [
    "unevaluable-supplier",
    "Late deliveries",
    "2 assessed · 2 matching · 0 assessed outside threshold · 1 unevaluable",
  ],
  [
    "unevaluable-all",
    "Late deliveries",
    "0 assessed · 0 matching · 0 assessed outside threshold · 3 unevaluable",
  ],
]) {
  test(`${fixture}: real population quality reaches the browser`, async ({
    page,
  }) => {
    await page.route("**/api/**", (route) =>
      route.continue({
        url: route.request().url().replace("/api/", `/cases/${fixture}/api/`),
      }),
    );
    await page.goto("/");
    await page.getByRole("button", { name: nav, exact: false }).first().click();
    await page
      .getByRole("button", { name: "Run analysis", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "Population quality" }),
    ).toContainText(expected);
    if (fixture === "unevaluable-all") {
      await expect(
        page.getByRole("heading", { name: "Review the available information" }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Save pending draft" }),
      ).toHaveCount(0);
    }
    await page.getByRole("button", { name: "Inspect source evidence" }).click();
    await expect(page.getByText("Snapshot integrity verified")).toBeVisible();
  });
}

test("stale forecast explains the gap and keeps editable inputs", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.continue({
      url: route.request().url().replace("/api/", "/cases/stale-forecast/api/"),
    }),
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Review the available information" }),
  ).toBeVisible();
  await expect(page.getByText(/stale history/i).first()).toBeVisible();
  await expect(page.getByText(/last observed: 2026-09-23/i)).toBeVisible();
  await expect(page.locator("form select").nth(0)).toHaveValue("P1");
  await expect(page.locator("form select").nth(1)).toHaveValue("W3");
  await page.locator("form select").nth(0).selectOption("P2");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(page.getByText("success", { exact: true })).toBeVisible();
});

test("stale revision keeps local note until the reviewer refreshes explicitly", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByLabel("Recommendation", { exact: true }).selectOption("r1");
  await page.getByLabel("Reviewer name").fill("Revision reviewer");
  await page.getByLabel("Review note").fill("Original saved note");
  await page.getByRole("button", { name: "Save pending draft" }).click();
  await page.getByText("Pending · revision 1").waitFor();
  const history = await (await page.request.get("/api/history")).json();
  const action = history.actions[0];
  const response = await page.request.post(
    `/api/proposals/${action.id}/decisions`,
    {
      headers: { "X-Sentinel-Client": "workspace" },
      data: {
        key: crypto.randomUUID(),
        decision: "edit",
        reviewer: "Other local window",
        expected_revision: 1,
        text: "New committed revision",
      },
    },
  );
  expect(response.status()).toBe(200);
  await page.getByLabel("Review note").fill("Preserved local investigation");
  await page.getByRole("button", { name: "Save edits" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Check saved revision" }),
  ).toBeVisible();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Preserved local investigation",
  );
  await page.getByRole("button", { name: "Check saved revision" }).click();
  await expect(page.getByText("Pending · revision 2")).toBeVisible();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Preserved local investigation",
  );
  await page.getByRole("button", { name: "Save edits" }).click();
  await expect(page.getByText("Pending · revision 3")).toBeVisible();
});
