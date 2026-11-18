/* global process, fetch, Buffer, console, chrome, document, window */
// Run with the cached Qwen provider already listening on port 8769.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout } from "node:timers/promises";
import { chromium } from "playwright";
import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const temporary = await mkdtemp(join(tmpdir(), "sentinel-live-"));
const proxyPort = 8771;
const backendPort = 8770;
const base = `http://127.0.0.1:${backendPort}`;
const backend = spawn(
  ".venv/bin/python",
  [
    "-m",
    "sentinel.web",
    "--init",
    "--data-dir",
    join(temporary, "workspace"),
    "--port",
    String(backendPort),
  ],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      SENTINEL_QWEN_URL: `http://127.0.0.1:${proxyPort}/v1/chat/completions`,
      SENTINEL_QWEN_TOKEN: "",
      SENTINEL_QWEN_MODEL: "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    },
  },
);

let context;
let proxy;
try {
  const extension = join(temporary, "zoom");
  await mkdir(extension);
  await writeFile(
    join(extension, "manifest.json"),
    JSON.stringify({
      manifest_version: 3,
      name: "Local browser zoom check",
      version: "1.0.0",
      background: { service_worker: "background.js" },
      permissions: ["tabs"],
    }),
  );
  await writeFile(
    join(extension, "background.js"),
    "chrome.runtime.onInstalled.addListener(() => {});",
  );
  const cached = join(
    homedir(),
    "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  );
  for (let attempt = 0; attempt < 100; attempt++) {
    if (backend.exitCode !== null)
      throw new Error("Disposable Sentinel server exited.");
    try {
      if ((await fetch(base + "/api/published/catalog")).ok) break;
    } catch {
      /* Backend is still starting. */
    }
    await setTimeout(150);
  }
  context = await chromium.launchPersistentContext(join(temporary, "browser"), {
    executablePath:
      process.env.CHROMIUM_PATH ?? (existsSync(cached) ? cached : undefined),
    channel: "chromium",
    viewport: null,
    headless: true,
    args: [
      "--window-size=1440,1000",
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
    ],
  });
  const page = context.pages()[0];
  await page.goto(base);
  await page.getByRole("button", { name: "Published data" }).click();
  const run = () =>
    page.getByRole("button", { name: "Run published analysis" }).click();
  await run();
  await expect(
    page.getByRole("table", {
      name: "Daily forecasts and seasonal comparison",
    }),
  ).toHaveCount(1);
  await expect(page.getByText("108.36", { exact: true }).first()).toBeVisible();
  const task = page.getByLabel("Task", { exact: true });
  await task.selectOption("coverage");
  await run();
  await expect(page.getByText(/days at \+20% sales/)).toBeVisible();
  await task.selectOption("lateness");
  await run();
  await expect(page.getByText(/assessed line items were late/)).toBeVisible();
  await task.selectOption("shipment");
  await run();
  await expect(
    page.getByText(/Sigmoid-calibrated logistic regression scored/),
  ).toBeVisible();
  await task.selectOption("forecast");
  await run();
  await expect(
    page.getByRole("heading", { name: "Untouched final evaluation" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Ask Qwen" }).click();
  const question = "Explain the forecast estimate and what it means.";
  const message = page.getByRole("textbox", { name: "Message" });
  await message.fill(question);
  const failed = page.waitForResponse((response) =>
    response.url().endsWith("/api/chat"),
  );
  await page.getByRole("button", { name: "Send message" }).click();
  assert.equal((await failed).status(), 503);
  await expect(message).toHaveValue(question);

  // Restore connectivity. This relay forwards the request to real Qwen, with no stubbed output.
  proxy = createServer(async (request, response) => {
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const upstream = await fetch(
        "http://127.0.0.1:8769/v1/chat/completions",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: Buffer.concat(chunks),
        },
      );
      response.writeHead(upstream.status, {
        "Content-Type": "application/json",
      });
      response.end(await upstream.text());
    } catch {
      response.writeHead(503).end();
    }
  });
  proxy.listen(proxyPort, "127.0.0.1");
  await once(proxy, "listening");
  await page.getByRole("button", { name: "Retry message" }).click();
  await expect(
    page.getByText("Qwen/Qwen2.5-Coder-1.5B-Instruct", { exact: true }),
  ).toBeVisible({ timeout: 48000 });
  await expect(page.getByText(/estimate is 108.4 units sold/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Ask Qwen" })).toBeFocused();
  console.log(
    "Browser live pipeline passed for forecast, coverage, lateness, held-out risk, and real Qwen after HTTP 503.",
  );

  const worker =
    context.serviceWorkers()[0] ??
    (await context.waitForEvent("serviceworker"));
  const zoom = await worker.evaluate(async (origin) => {
    const tabs = await chrome.tabs.query({});
    const tab = tabs.find((item) => item.url?.startsWith(origin));
    await chrome.tabs.setZoom(tab.id, 2);
    return chrome.tabs.getZoom(tab.id);
  }, base);
  assert.equal(zoom, 2);
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await expect(page.locator(".map-node")).toHaveCount(5);
  await page.getByRole("button", { name: "Fit view" }).click();
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  await page.getByRole("button", { name: "Ask Qwen" }).click();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeFocused();
  assert.deepEqual(
    (await new AxeBuilder({ page }).include("dialog").analyze()).violations.map(
      ({ id }) => id,
    ),
    [],
  );
  console.log(
    "Chromium native browser zoom 200% passed for map and chat, including scoped axe scan.",
  );
} finally {
  await context?.close();
  proxy?.close();
  if (backend.exitCode === null) {
    backend.kill();
    await once(backend, "exit");
  }
  await rm(temporary, { recursive: true, force: true });
}
