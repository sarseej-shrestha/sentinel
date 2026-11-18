import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("sidebar and theme persist while keyboard map zoom stays bounded", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  const collapse = page.getByRole("button", { name: "Collapse sidebar" });
  await collapse.focus();
  await page.keyboard.press("Enter");
  const expand = page.getByRole("button", { name: "Expand sidebar" });
  await expect(expand).toBeFocused();
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "Light theme" }).click();
  await page.reload();
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await expect(page.locator(".map-node")).toHaveCount(5);
  await page.getByRole("button", { name: "Reset map zoom" }).click();
  await expect(page.getByText("100%", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Zoom in" }).focus();
  for (let i = 0; i < 9; i++) await page.keyboard.press("Enter");
  await expect(page.getByText("200%", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Fit view" }).click();
  await expect(page.getByText("200%", { exact: true })).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 360, height: 640 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(360);
});

test("chat traps focus, retains text after failure and retries", async ({
  page,
}) => {
  let attempts = 0;
  await page.route("**/api/chat", async (route) => {
    attempts++;
    const status = attempts === 1 ? 503 : 200;
    const json =
      attempts === 1
        ? { detail: "Qwen is unavailable. Retry." }
        : {
            model: "Test double",
            answer: "Inspect the selected source.",
            citations: [],
          };
    await route.fulfill({ status, json });
  });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Ask Qwen" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Sentinel assistant" });
  const message = dialog.getByRole("textbox", { name: "Message" });
  await expect(message).toBeFocused();
  await message.fill("Where is the evidence?");
  await dialog.getByRole("button", { name: "Send message" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Qwen is unavailable");
  await expect(message).toHaveValue("Where is the evidence?");
  await dialog.getByRole("button", { name: "Retry message" }).click();
  await expect(dialog.getByText("Inspect the selected source.")).toBeVisible();
  await expect(message).toHaveValue("");
  expect(
    (await new AxeBuilder({ page }).include("dialog").analyze()).violations,
  ).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("CSV preview creates a separate persisted map and inspectable wire", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await page.getByRole("button", { name: "Import relationships" }).click();
  await page.getByLabel("Relationship file").setInputFiles({
    name: "Browser depot.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "source_type,source_id,relationship,target_type,target_id,source_label,target_label\n" +
        "supplier,T1,supplies,warehouse,D1,Test supplier,Test depot\n" +
        "supplier,T1,supplies,warehouse,D1,Conflicting supplier,Test depot\n",
    ),
  });
  await page.getByRole("button", { name: "Validate and preview" }).click();
  await expect(
    page.getByText("1 accepted · 1 rejected · 2 nodes · 1 relationships"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create map", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Browser depot" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await page
    .getByRole("button", { name: /supplier:T1.*warehouse:D1.*supplies/ })
    .click();
  await page
    .getByRole("button", { name: "View matching source record" })
    .click();
  await expect(page.getByText("Source record fields")).toBeVisible();
  await expect(
    page.getByText("Browser depot.csv", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await expect(page.locator(".map-node")).toHaveCount(5);
  await page
    .getByRole("combobox", { name: "Map", exact: true })
    .selectOption({ label: "Browser depot" });
  await expect(page.locator(".map-node")).toHaveCount(2);
});
