#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

function option(name, fallback = undefined) {
  const prefix = `${name}=`;
  const argument = process.argv.find((value) => value.startsWith(prefix));
  return argument ? argument.slice(prefix.length) : fallback;
}

function required(name) {
  const value = option(name);
  if (value === undefined) throw new Error(`Missing required option ${name}=...`);
  return value;
}

async function main() {
  let chromium;
  try {
    ({ chromium } = require("playwright"));
  } catch (error) {
    throw new Error("Playwright is required in the Node runtime", { cause: error });
  }

  const html = path.resolve(required("--html"));
  const timelineId = required("--timeline-id");
  const start = Number(required("--start"));
  const frames = Number(required("--frames"));
  const fps = Number(option("--fps", "30"));
  const out = path.resolve(required("--out"));
  const width = Number(option("--width", "1920"));
  const height = Number(option("--height", "1080"));
  const waitFrame = process.argv.includes("--wait-frame");
  const seekFrames = option("--seek-frames", "")
    .split(",")
    .filter(Boolean)
    .map(Number);

  for (const [label, value] of Object.entries({ start, frames, fps, width, height })) {
    if (!Number.isFinite(value)) throw new Error(`${label} must be numeric`);
  }
  if (!Number.isInteger(frames) || frames < 1) throw new Error("frames must be a positive integer");
  if (fps <= 0 || width < 1 || height < 1) throw new Error("fps and dimensions must be positive");

  fs.mkdirSync(out, { recursive: true });
  for (const fileName of fs.readdirSync(out)) {
    if (/^frame-\d+\.png$/.test(fileName)) fs.unlinkSync(path.join(out, fileName));
  }

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(html).href, { waitUntil: "load" });
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.evaluate((id) => {
    const timeline = window.__timelines && window.__timelines[id];
    if (!timeline || typeof timeline.time !== "function") {
      throw new Error(`Registered production timeline not found: ${id}`);
    }
    if (typeof timeline.pause === "function") timeline.pause(0);
  }, timelineId);

  const captures = [];
  for (let frameOffset = 0; frameOffset < frames; frameOffset += 1) {
    const time = start + frameOffset / fps;
    const output = path.join(out, `frame-${String(frameOffset + 1).padStart(6, "0")}.png`);
    await page.evaluate(
      ({ id, targetTime }) => window.__timelines[id].time(targetTime),
      { id: timelineId, targetTime: time },
    );
    if (waitFrame) await page.evaluate(() => new Promise(requestAnimationFrame));
    await page.screenshot({ path: output });
    captures.push({ frame: frameOffset + 1, time, output });
  }

  const seekDir = path.join(out, "seek");
  const seeks = [];
  if (seekFrames.length > 0) fs.mkdirSync(seekDir, { recursive: true });
  for (const frameOffset of seekFrames) {
    if (!Number.isInteger(frameOffset) || frameOffset < 0 || frameOffset >= frames) {
      throw new Error(`Invalid --seek-frames entry: ${frameOffset}`);
    }
    const time = start + frameOffset / fps;
    const output = path.join(seekDir, `frame-${String(frameOffset + 1).padStart(6, "0")}.png`);
    await page.evaluate(
      ({ id, targetTime }) => window.__timelines[id].time(targetTime),
      { id: timelineId, targetTime: time },
    );
    if (waitFrame) await page.evaluate(() => new Promise(requestAnimationFrame));
    await page.screenshot({ path: output });
    seeks.push({ frame: frameOffset + 1, time, output });
  }

  await browser.close();
  const manifest = {
    html,
    timeline_id: timelineId,
    start,
    frames,
    fps,
    width,
    height,
    wait_frame: waitFrame,
    captures,
    seeks,
  };
  fs.writeFileSync(path.join(out, "timeline-capture-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ ok: true, out, frames, seeks: seeks.length }, null, 2)}\n`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
