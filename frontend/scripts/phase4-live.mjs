/* global process, fetch, Buffer, console, chrome, document, window */
// Run with the local Ollama Qwen provider already listening.
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

const provider =
  process.env.SENTINEL_LIVE_QWEN_URL ??
  "http://127.0.0.1:11434/v1/chat/completions";
const model =
  process.env.SENTINEL_LIVE_QWEN_MODEL ?? "qwen3:4b-instruct-2507-q4_K_M";
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
      SENTINEL_QWEN_MODEL: model,
    },
  },
);

let context;
let proxy;
let fault;
let forwardCount = 0;
let holdNextReply = false;
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

  // Restore connectivity. Fault cases replace exactly one completed provider envelope; repairs use real Qwen.
  proxy = createServer(async (request, response) => {
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      forwardCount++;
      const hold = holdNextReply;
      holdNextReply = false;
      const upstream = await fetch(provider, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: Buffer.concat(chunks),
      });
      let text = await upstream.text();
      if (hold) await setTimeout(1000);
      if (fault && upstream.ok) {
        text = JSON.stringify({ choices: [fault] });
        fault = undefined;
      }
      response.writeHead(upstream.status, {
        "Content-Type": "application/json",
      });
      response.end(text);
    } catch {
      response.writeHead(503).end();
    }
  });
  proxy.listen(proxyPort, "127.0.0.1");
  await once(proxy, "listening");
  await page.getByRole("button", { name: "Retry message" }).click();
  await expect(page.getByText(model, { exact: true })).toBeVisible({
    timeout: 48000,
  });
  await expect(page.getByText(/estimate 108.36 units sold/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Ask Qwen" })).toBeFocused();
  console.log(
    "Browser live pipeline passed for forecast, coverage, lateness, held-out risk, and real Qwen after HTTP 503.",
  );

  // Start a real request, close the modal, and navigate before it can render.
  await page.getByRole("button", { name: "Ask Qwen" }).click();
  holdNextReply = true;
  const started = page.waitForRequest((request) =>
    request.url().endsWith("/api/chat"),
  );
  await message.fill(
    "Compare the forecast against the seasonal-naive results and explain the limitations.",
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await started;
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Supply Chain Map" }).click();
  await page.getByRole("button", { name: "Ask Qwen" }).click();
  await expect(
    page.getByText("Context · Map · no record selected"),
  ).toBeVisible();
  await expect(
    page
      .locator(".chat-turns")
      .getByText(
        "Compare the forecast against the seasonal-naive results and explain the limitations.",
        { exact: true },
      ),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "New conversation" }).click();

  // Cancel an active request and send a new general question with the same dialog.
  await message.fill("What is cycle stock?");
  const pending = page.waitForRequest((request) =>
    request.url().endsWith("/api/chat"),
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await pending;
  await page.getByRole("button", { name: "Cancel request" }).click();
  await expect(message).toHaveValue("What is cycle stock?");
  await message.fill("What does days of coverage mean?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".chat-answer").last()).toContainText(/days/i, {
    timeout: 50000,
  });
  await expect(
    page.getByText("What is cycle stock?", { exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");

  const headers = {
    "Content-Type": "application/json",
    "X-Sentinel-Client": "workspace",
  };
  const saved = await fetch(base + "/api/analyses", {
    method: "POST",
    headers,
    body: JSON.stringify({
      kind: "supplier_delay",
      supplier_id: "S2",
      start_date: "2026-08-01",
      end_date: "2026-08-31",
    }),
  }).then((response) => response.json());
  for (const injected of [
    {
      message: { content: "This unsupported order is overdue. [[F900]]" },
      finish_reason: "stop",
    },
    {
      message: {
        tool_calls: [
          { function: { name: "captured_records", arguments: '{"offset":' } },
        ],
      },
      finish_reason: "tool_calls",
    },
  ]) {
    fault = injected;
    const before = forwardCount;
    const response = await fetch(base + "/api/chat", {
      method: "POST",
      headers,
      body: JSON.stringify({
        message: "What supports this supplier result?",
        analysis_id: saved.analysis_id,
      }),
    });
    assert.equal(response.status, 200);
    const answer = await response.json();
    assert.equal(answer.model, model);
    assert.equal(forwardCount - before, 2);
    assert.match(answer.verified.join(" "), /Supplier B|observed deliveries/);
    assert(!answer.answer.includes("unsupported order"));
    assert(
      answer.citations.every(({ scope }) => scope.target_id === "supplier:S2"),
    );
  }
  console.log(
    "Real-provider browser context switch and cancellation passed. Invalid reference and truncated tool arguments each repaired with one live generation after controlled injection.",
  );

  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Supplier", exact: true })
    .selectOption("S2");
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  await expect(page.locator(".result-lead")).toContainText("7 late / 31");
  await page.getByRole("button", { name: "Ask Qwen" }).click();
  await message.fill("Show me the records behind that number.");
  await page.getByRole("button", { name: "Send message" }).click();
  const captured = page.getByRole("button", {
    name: "Open captured source records",
    exact: true,
  });
  await expect(captured).toBeVisible({ timeout: 50000 });
  await captured.click();
  const evidence = page.getByRole("dialog", {
    name: "Inspect source evidence",
  });
  await expect(evidence.getByLabel("Evidence target")).toHaveValue(
    "supplier:S2",
  );
  await page.keyboard.press("Escape");
  console.log(
    "The real Qwen reference opened the original Supplier B target in captured evidence.",
  );

  const proposal = await fetch(base + "/api/proposals", {
    method: "POST",
    headers,
    body: JSON.stringify({
      key: "workspace-assistant-smoke",
      analysis_id: saved.analysis_id,
      recommendation_id: saved.recommendations[0].id,
      reviewer: "Live fixture reviewer",
      text: "Inspect the saved records.",
    }),
  }).then((response) => response.json());
  await page.getByRole("button", { name: "History", exact: true }).click();
  await page.getByRole("button", { name: "Ask Qwen" }).click();
  await message.fill("Draft a note for the saved Supplier B review.");
  await page.getByRole("button", { name: "Send message" }).click();
  const useDraft = page.getByRole("button", {
    name: "Use proposed note in existing review",
  });
  await expect(useDraft).toBeVisible({ timeout: 50000 });
  await useDraft.click();
  await expect(page.getByText("Proposed assistant note")).toBeVisible();
  const note = page.getByLabel("Review note");
  await expect(note).toHaveValue("Inspect the saved records.");
  await page.getByRole("button", { name: "Use proposed wording" }).click();
  await expect(note).toHaveValue(/7 late of 31/);
  const proposed = await note.inputValue();
  const beforeSave = await fetch(base + "/api/proposals/" + proposal.id).then(
    (response) => response.json(),
  );
  assert.equal(beforeSave.revision, 1);
  await page.route(
    "**/api/proposals/*/decisions",
    (route) =>
      route.fulfill({
        status: 503,
        json: { detail: "Injected save failure" },
      }),
    { times: 1 },
  );
  const saveEdits = page.getByRole("button", {
    name: "Save edits",
    exact: true,
  });
  await saveEdits.click();
  await expect(page.getByText("Injected save failure")).toBeVisible();
  await expect(note).toHaveValue(proposed);
  await saveEdits.click();
  await expect(page.getByText(/Pending · revision 2/)).toBeVisible();
  const afterSave = await fetch(base + "/api/proposals/" + proposal.id).then(
    (response) => response.json(),
  );
  assert.equal(afterSave.text, proposed);
  console.log(
    "Draft proposal, explicit save, failed-save preservation, and retry passed against the existing review service.",
  );

  await page.getByRole("button", { name: "Ask Qwen" }).click();
  await message.fill("Open the spreadsheet import dialog");
  await page.getByRole("button", { name: "Send message" }).click();
  const openImport = page.getByRole("button", {
    name: "Open import",
    exact: true,
  });
  await expect(openImport).toBeVisible({ timeout: 50000 });
  await openImport.click();
  await expect(page.getByLabel("Relationship file")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Download blank template" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cancel import" }).click();
  console.log(
    "A validated Qwen action opened the existing import flow and template.",
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
