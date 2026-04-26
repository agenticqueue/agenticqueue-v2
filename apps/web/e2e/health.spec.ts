import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type HealthStatus = {
  status: "ok";
  timestamp: string;
};

type VersionInfo = {
  version: string;
  commit: string;
  built_at: string;
};

const artifactDir = path.resolve(
  __dirname,
  "../../../plans/v2-rebuild/artifacts/cap-01",
);

test("renders health and version payloads", async ({ page, request }) => {
  const apiBase = process.env.AQ_API_URL ?? "http://localhost:8001";
  const startedAt = Date.now();
  const [healthResponse, versionResponse] = await Promise.all([
    request.get(`${apiBase}/healthz`),
    request.get(`${apiBase}/version`),
  ]);
  expect(healthResponse.ok()).toBe(true);
  expect(versionResponse.ok()).toBe(true);

  const health = (await healthResponse.json()) as HealthStatus;
  const version = (await versionResponse.json()) as VersionInfo;

  await page.goto("/");

  await expect(page.getByTestId("health-status")).toHaveText(health.status);
  await expect(page.getByTestId("version-version")).toHaveText(version.version);
  await expect(page.getByTestId("version-commit")).toHaveText(version.commit);
  await expect(page.getByTestId("version-built-at")).toHaveText(version.built_at);

  const renderedTimestamp = await page.getByTestId("health-timestamp").innerText();
  expect(Number.isNaN(Date.parse(renderedTimestamp))).toBe(false);
  expect(Math.abs(Date.parse(renderedTimestamp) - startedAt)).toBeLessThanOrEqual(
    5_000,
  );

  fs.mkdirSync(artifactDir, { recursive: true });
  fs.writeFileSync(
    path.join(artifactDir, "ui-health.html"),
    await page.content(),
  );
  await page.screenshot({
    path: path.join(artifactDir, "ui-health.png"),
    fullPage: true,
  });
});
