import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("Phase 3 Analyze answer, mock recovery and mobile navigation", async ({
  page,
}) => {
  const folder = process.env.PHASE3_SCREEN_DIR;
  await page.goto("/");
  if (folder)
    await page.screenshot({ path: `${folder}/Analyze_Initial_1440x1000.png` });
  await page
    .getByRole("combobox", { name: "Supplier", exact: true })
    .selectOption("S2");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(page.locator(".result-lead")).toContainText(
    "7 late / 31 evaluable",
  );
  await expect(page.locator(".result-lead")).toContainText(
    "2026-08-01 through 2026-08-31",
  );
  if (folder)
    await page.screenshot({
      path: `${folder}/Analyze_After_S2_Aug_1440x1000.png`,
      fullPage: true,
    });
  await page.getByRole("button", { name: /View evidence/ }).click();
  const dialog = page.getByRole("dialog", { name: "Inspect source evidence" });
  await expect(dialog.getByLabel("Evidence target")).toHaveValue("supplier:S2");
  if (folder)
    await page.screenshot({ path: `${folder}/Evidence_S2_1440x1000.png` });
  await page.keyboard.press("Escape");
  await page.getByText("Study response controls").click();
  await page.getByLabel("Next response (one use)").selectOption("uncertain");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByText(/Study simulation: available information is insufficient/),
  ).toBeVisible();
  if (folder)
    await page.screenshot({
      path: `${folder}/Analyze_Uncertain_1440x1000.png`,
    });
  await page.getByLabel("Next response (one use)").selectOption("error");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByText(
      /Study simulation: the analysis service is temporarily unavailable/,
    ),
  ).toBeVisible();
  if (folder)
    await page.screenshot({ path: `${folder}/Analyze_Error_1440x1000.png` });
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(page.locator(".result-lead")).toContainText(
    "7 late / 31 evaluable",
  );
  await page.getByLabel("Next response (one use)").selectOption("slow");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(
    page.getByText(/Checking the request, running read-only analysis/),
  ).toBeVisible();
  await expect(page.locator(".result-lead")).toContainText(
    "7 late / 31 evaluable",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Supply Chain Map" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await expect(page.locator(".map-node")).toHaveCount(5);
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Relationship list" }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  if (folder)
    await page.screenshot({
      path: `${folder}/Map_Mobile_390x844.png`,
      fullPage: true,
    });
});
