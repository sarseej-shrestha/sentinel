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
