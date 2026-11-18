import { test, expect } from "@playwright/test";

test("connected map exposes recorded evidence, moves wires and keeps exploratory links separate", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await expect(page.locator(".map-node")).toHaveCount(5);
  await expect(page.locator(".map-wire.recorded")).toHaveCount(5);
  await page.getByRole("button", { name: "Relationships" }).click();
  await page
    .getByRole("button", {
      name: /shipment:SH367.*warehouse:W2.*destination warehouse/,
    })
    .click();
  await page.getByRole("button", { name: "Fit view" }).click();
  await page
    .getByRole("button", { name: "View matching source record" })
    .click();
  await expect(page.locator(".map-record")).toContainText("S2");
  await expect(page.locator(".map-record")).toContainText("W2");
  if (process.env.PHASE3_SCREEN_DIR) {
    await page.screenshot({
      path: `${process.env.PHASE3_SCREEN_DIR}/Map_Inspector_1440x1000.png`,
    });
  }
  const wire = page.locator(".map-wire.recorded").first();
  const before = await wire.getAttribute("d");
  const shipment = page.getByRole("button", {
    name: /Select and drag shipment SH367/,
  });
  const box = await shipment.boundingBox();
  if (!box) throw new Error("Shipment box missing");
  await page.mouse.move(box.x + 45, box.y + 35);
  await page.mouse.down();
  await page.mouse.move(box.x + 105, box.y + 85, { steps: 6 });
  await page.mouse.up();
  expect(await wire.getAttribute("d")).not.toBe(before);
  await page
    .getByRole("button", { name: /Select and drag supplier Supplier B/ })
    .click();
  await page.getByLabel("Connect to…").selectOption("warehouse:W2");
  await page.getByRole("button", { name: "Connect to selected box" }).click();
  await expect(page.locator(".map-wire.exploratory")).toHaveCount(1);
  await expect(
    page.getByText("No source record supports this user-created wire"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Remove exploratory connection" })
    .click();
  await expect(page.locator(".map-wire.exploratory")).toHaveCount(0);
  const output = await page
    .locator('[data-socket-node="supplier:S2"].output')
    .boundingBox();
  const input = await page
    .locator('[data-socket-node="order_item:OI367"].input')
    .boundingBox();
  if (!output || !input) throw new Error("Connection sockets missing");
  await page.mouse.move(
    output.x + output.width / 2,
    output.y + output.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(input.x + input.width / 2, input.y + input.height / 2, {
    steps: 8,
  });
  await page.mouse.up();
  await expect(page.locator(".map-wire.exploratory")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Remove exploratory connection" })
    .click();
  await page
    .getByRole("button", { name: /Select and drag order_item OI367/ })
    .click();
  await page.getByRole("button", { name: "Expand one step" }).click();
  await expect(
    page.getByRole("button", { name: /Select and drag product.*P2/ }),
  ).toBeVisible();
  await page.getByLabel("Find a record").fill("Supplier B");
  await page.getByRole("button", { name: "Find", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /supplier: Supplier B.*valid/ }),
  ).toBeVisible();
});
