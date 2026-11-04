import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("Supplier B recommendation opens its own source records live and after replay", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Late deliveries", exact: false })
    .click();
  await page.locator("form select").first().selectOption("");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByLabel("Recommendation", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Recommendation", { exact: true }).selectOption("r2");

  async function expectSupplierB() {
    await page.getByRole("button", { name: "Inspect source evidence" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Inspect source evidence",
    });
    await expect(dialog.getByLabel("Evidence target")).toHaveValue(
      "supplier:S2",
    );
    const rows = dialog
      .getByRole("region", { name: "Captured transaction records" })
      .locator("tbody tr");
    await expect(rows.first()).toContainText("S2");
    if (process.env.SENTINEL_CAPTURE_DIR) {
      await rows.first().scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `${process.env.SENTINEL_CAPTURE_DIR}/supplier-b-evidence.png`,
      });
    }
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await dialog.getByRole("button", { name: "Next source page" }).click();
    await expect(dialog.getByText("Source records 11–20 of 31")).toBeVisible();
    for (const row of await rows.all()) {
      await expect(row).toContainText("S2");
      await expect(row).not.toContainText("S1");
    }
    await dialog.getByLabel("Evidence target").selectOption("supplier:S1");
    await expect(rows.first()).toContainText("S1");
    await page.keyboard.press("Escape");
  }

  await expectSupplierB();
  await page.getByLabel("Reviewer name").fill("Participant readiness reviewer");
  await page.getByLabel("Review note").fill("Follow up Supplier B records.");
  await page.getByRole("button", { name: "Save pending draft" }).click();
  await expect(page.getByText("Pending · revision 1")).toBeVisible();
  await page.getByRole("button", { name: "Inventory coverage" }).click();
  await page
    .getByRole("button", { name: "Late deliveries", exact: false })
    .click();
  await expect(page.getByText("Pending · revision 1")).toBeVisible();
  await expectSupplierB();
  await page.getByRole("button", { name: "Approve simulation" }).click();
  await page.getByRole("button", { name: "Confirm approval" }).click();
  await expect(
    page.getByText(/Simulation approved · revision 2/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Decision history", exact: true })
    .click();
  await expect(page.getByText(/audit events verified/)).toBeVisible();
  await page.getByRole("button", { name: "Open review" }).first().click();
  await expect(page.getByText(/Saved snapshot/)).toBeVisible();
  await expect(page.getByLabel("Review note")).toBeDisabled();
  await expectSupplierB();
});

test("slow evidence paging retains focus across source and aggregate pages", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByRole("button", { name: "Inspect source evidence" }).click();
  const dialog = page.getByRole("dialog", { name: "Inspect source evidence" });
  await expect(dialog.getByText("Snapshot integrity verified")).toBeVisible();
  let failSource = false;
  await page.route("**/api/evidence/**/targets/**", async (route) => {
    if (route.request().url().includes("offset=10")) {
      await new Promise((resolve) => setTimeout(resolve, 650));
      if (failSource) {
        failSource = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Injected source page failure" }),
        });
        return;
      }
    }
    await route.continue();
  });
  const sourceNext = dialog.getByRole("button", { name: "Next source page" });
  await sourceNext.focus();
  await page.keyboard.press("Enter");
  await expect(
    dialog.getByText(/Verifying captured transaction records/),
  ).toBeVisible();
  await expect(sourceNext).toBeFocused();
  await expect(dialog.getByText("Source records 11–20 of 31")).toBeVisible();
  await expect(sourceNext).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByText("Provenance and query details")).toBeFocused();
  const sourcePrevious = dialog.getByRole("button", {
    name: "Previous source page",
  });
  await sourcePrevious.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText("Source records 1–10 of 31")).toBeVisible();
  await expect(sourcePrevious).toBeFocused();
  failSource = true;
  await sourceNext.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText("Injected source page failure")).toBeVisible();
  await expect(sourceNext).toBeFocused();
  await expect(dialog.getByText("Source records 1–10 of 31")).toBeVisible();
  await dialog.getByRole("button", { name: "Retry source page" }).click();
  await expect(dialog.getByText("Source records 11–20 of 31")).toBeVisible();
  await sourceNext.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText("Source records 21–30 of 31")).toBeVisible();
  await sourceNext.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText("Source records 31–31 of 31")).toBeVisible();
  await expect(sourceNext).toBeFocused();
  await expect(sourceNext).toHaveAttribute("aria-disabled", "true");
  await sourcePrevious.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText("Source records 21–30 of 31")).toBeVisible();
  await expect(sourcePrevious).toBeFocused();
  await page.unroute("**/api/evidence/**/targets/**");
  await page.keyboard.press("Escape");

  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByRole("button", { name: "Inspect source evidence" }).click();
  await expect(dialog.getByText("Snapshot integrity verified")).toBeVisible();
  let failAggregate = false;
  await page.route("**/api/evidence/**", async (route) => {
    if (route.request().url().includes("offset=25")) {
      await new Promise((resolve) => setTimeout(resolve, 650));
      if (failAggregate) {
        failAggregate = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Injected aggregate page failure" }),
        });
        return;
      }
    }
    await route.continue();
  });
  const next = dialog.getByRole("button", { name: "Next", exact: true });
  await next.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText(/Verifying the snapshot/)).toBeVisible();
  await expect(next).toBeFocused();
  await expect(dialog.getByText("26–50 of 180")).toBeVisible();
  await expect(next).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByLabel("Evidence target")).toBeFocused();
  const previous = dialog.getByRole("button", {
    name: "Previous",
    exact: true,
  });
  await previous.focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByText("1–25 of 180")).toBeVisible();
  await expect(previous).toBeFocused();
  failAggregate = true;
  await next.focus();
  await page.keyboard.press("Enter");
  await expect(
    dialog.getByText("Injected aggregate page failure"),
  ).toBeVisible();
  await expect(next).toBeFocused();
  await expect(dialog.getByText("1–25 of 180")).toBeVisible();
  await dialog.getByRole("button", { name: "Retry evidence page" }).click();
  await expect(dialog.getByText("26–50 of 180")).toBeVisible();
  await next.focus();
  await page.keyboard.press("Enter");
  await dialog.getByLabel("Evidence target").focus();
  await expect(dialog.getByText("51–75 of 180")).toBeVisible();
  await expect(dialog.getByLabel("Evidence target")).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("saved draft focuses its pending state and failed save keeps retry reachable", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByLabel("Recommendation", { exact: true }).selectOption("r1");
  await page.getByLabel("Reviewer name").fill("Keyboard reviewer");
  await page.getByLabel("Review note").fill("Keep this note after an error.");
  let fail = true;
  await page.route("**/api/proposals", async (route) => {
    if (fail) {
      fail = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Injected save failure" }),
      });
    } else await route.continue();
  });
  const save = page.getByRole("button", { name: "Save pending draft" });
  await save.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Injected save failure")).toBeVisible();
  await expect(page.getByLabel("Review note")).toHaveValue(
    "Keep this note after an error.",
  );
  await expect(save).toBeFocused();
  await page.keyboard.press("Enter");
  const pending = page.getByText("Pending · revision 1");
  await expect(pending).toBeVisible();
  await expect(pending).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("all-zero forecast draws finite zero lines and keeps fourteen table rows", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.continue({
      url: route.request().url().replace("/api/", "/cases/zero-forecast/api/"),
    }),
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByRole("img", { name: /Fourteen-day demand forecast/ }),
  ).toBeVisible();
  const chart = page.locator(".forecast-chart");
  expect(await chart.evaluate((node) => node.outerHTML)).not.toMatch(
    /NaN|Infinity/,
  );
  await expect(chart.locator("polyline")).toHaveCount(2);
  await page.getByText("View forecast data table").click();
  const rows = page
    .getByRole("table", {
      name: "Daily forecast and baseline, in units per day",
    })
    .locator("tbody tr");
  await expect(rows).toHaveCount(14);
  await expect(rows.first()).toContainText("2026-10-01");
  await expect(rows.last()).toContainText("2026-10-14");
  for (const row of await rows.all()) await expect(row).toContainText("0");
  if (process.env.SENTINEL_CAPTURE_DIR)
    await page.screenshot({
      path: `${process.env.SENTINEL_CAPTURE_DIR}/zero-forecast.png`,
      fullPage: true,
    });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.unroute("**/api/**");
  await page.goto("/");
  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  const ordinary = page.locator(".forecast-chart");
  await expect(ordinary).toBeVisible();
  expect(await ordinary.evaluate((node) => node.outerHTML)).not.toMatch(
    /NaN|Infinity/,
  );
  await page.getByText("View forecast data table").click();
  await expect(
    page
      .getByRole("table", {
        name: "Daily forecast and baseline, in units per day",
      })
      .locator("tbody tr")
      .first(),
  ).toContainText("14.43");
});

test("history rows and controls identify task, scope and saved follow-up", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page
    .getByRole("button", { name: "Demand forecast", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page
    .getByRole("button", { name: "Late deliveries", exact: false })
    .click();
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await page.getByLabel("Recommendation", { exact: true }).selectOption("r2");
  await page.getByLabel("Reviewer name").fill("History reviewer");
  await page.getByLabel("Review note").fill("Supplier B follow-up");
  await page.getByRole("button", { name: "Save pending draft" }).click();
  await page
    .getByRole("button", { name: "Decision history", exact: true })
    .click();
  await expect(page.getByText(/audit events verified/)).toBeVisible();
  await expect(
    page
      .getByRole("button", {
        name: /Replay snapshot.*Demand forecast.*P1.*W3/,
      })
      .first(),
  ).toBeVisible();
  const lateReplays = page.getByRole("button", {
    name: /Replay snapshot.*Supplier late deliveries.*All suppliers/,
  });
  expect(await lateReplays.count()).toBeGreaterThanOrEqual(2);
  const replayNames = await lateReplays.evaluateAll((buttons) =>
    buttons.map((button) => button.getAttribute("aria-label")),
  );
  expect(new Set(replayNames).size).toBe(replayNames.length);
  expect(replayNames.every((name) => name?.includes("Reference "))).toBe(true);
  await expect(
    page
      .getByRole("button", { name: /Open review.*Supplier B.*pending/i })
      .first(),
  ).toBeVisible();
  await page.setViewportSize({ width: 320, height: 700 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page
    .getByRole("button", {
      name: /Replay snapshot.*Demand forecast.*P1.*W3/,
    })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Demand forecast" }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
