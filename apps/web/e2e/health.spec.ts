import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type HealthStatus = {
  status: "ok";
  timestamp: string;
};

const artifactDir = path.resolve(
  __dirname,
  "../../../plans/v2-rebuild/artifacts/cap-02",
);

test("public health proxy works and root routes to login", async ({
  page,
  request,
}) => {
  const healthResponse = await request.get("/api/health");
  expect(healthResponse.ok()).toBe(true);

  const health = (await healthResponse.json()) as HealthStatus;
  expect(health.status).toBe("ok");
  expect(Number.isNaN(Date.parse(health.timestamp))).toBe(false);

  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId("login-key")).toBeVisible();

  fs.mkdirSync(artifactDir, { recursive: true });
  fs.writeFileSync(
    path.join(artifactDir, "web-health-smoke.html"),
    await page.content(),
  );
  await page.screenshot({
    path: path.join(artifactDir, "web-health-smoke.png"),
    fullPage: true,
  });
});
