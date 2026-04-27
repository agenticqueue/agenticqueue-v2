import { expect, test, type APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type SetupResponse = {
  actor_id: string;
  founder_key: string;
};

const artifactDir = path.resolve(
  __dirname,
  "../../../plans/v2-rebuild/artifacts/cap-02",
);
const sessionCookieName = "aq2_session";
let cachedKey: string | undefined;

test.describe.configure({ mode: "serial" });

async function resolveValidKey(request: APIRequestContext): Promise<string> {
  if (cachedKey) {
    return cachedKey;
  }
  if (process.env.AQ_WEB_TEST_KEY) {
    cachedKey = process.env.AQ_WEB_TEST_KEY;
    return cachedKey;
  }

  const apiBase = process.env.AQ_API_URL ?? "http://localhost:8001";
  const response = await request.post(`${apiBase}/setup`, { data: {} });
  if (!response.ok()) {
    throw new Error(
      "AQ_WEB_TEST_KEY is required when /setup has already been completed.",
    );
  }

  const payload = (await response.json()) as SetupResponse;
  cachedKey = payload.founder_key;
  return cachedKey;
}

test("cookie is httpOnly and whoami renders the authenticated actor", async ({
  context,
  page,
  request,
}) => {
  fs.mkdirSync(artifactDir, { recursive: true });
  const validKey = await resolveValidKey(request);

  await page.goto("/login");
  await page.getByTestId("login-key").fill(validKey);
  await page.getByTestId("login-submit").click();
  await page.waitForURL("**/whoami");

  await expect(page.getByTestId("whoami-actor-name")).toHaveText("founder");
  await expect(page.getByTestId("whoami-actor-kind")).toHaveText("human");
  await expect(page.getByTestId("whoami-actor-id")).not.toHaveText("");
  await expect(page.getByTestId("whoami-actor-created-at")).not.toHaveText("");

  const cookies = await context.cookies();
  const sessionCookie = cookies.find((cookie) => cookie.name === sessionCookieName);
  expect(sessionCookie).toBeDefined();
  expect(sessionCookie?.httpOnly).toBe(true);
  expect(sessionCookie?.sameSite).toBe("Strict");
  expect(sessionCookie?.secure).toBe(process.env.AQ_COOKIE_SECURE === "true");

  const documentCookie = await page.evaluate(() => document.cookie);
  expect(documentCookie).not.toContain(sessionCookieName);

  await page.screenshot({
    path: path.join(artifactDir, "whoami-screenshot.png"),
    fullPage: true,
  });

  const flagsPage = await context.newPage();
  await flagsPage.setContent(`
    <main>
      <h1>Cookie flags</h1>
      <dl>
        <dt>name</dt><dd>${sessionCookieName}</dd>
        <dt>httpOnly</dt><dd>${String(sessionCookie?.httpOnly)}</dd>
        <dt>sameSite</dt><dd>${sessionCookie?.sameSite ?? ""}</dd>
        <dt>secureMatchesEnv</dt><dd>${String(sessionCookie?.secure === (process.env.AQ_COOKIE_SECURE === "true"))}</dd>
        <dt>documentCookieVisible</dt><dd>${String(documentCookie.includes(sessionCookieName))}</dd>
      </dl>
    </main>
  `);
  await flagsPage.screenshot({
    path: path.join(artifactDir, "web-cookie-flags.png"),
    fullPage: true,
  });
  await flagsPage.close();
});

test("invalid-key login response is byte-equal to no-key login response", async ({
  request,
}) => {
  const invalid = await request.post("/login", {
    form: { key: "aq2_invalid_web_login_key_0000000000000000" },
  });
  const missing = await request.post("/login", { form: {} });

  expect(invalid.status()).toBe(401);
  expect(missing.status()).toBe(401);
  expect(await invalid.text()).toBe(await missing.text());
});

test("api actors me rejects requests without a session cookie", async ({ request }) => {
  const response = await request.get("/api/actors/me");
  expect(response.status()).toBe(401);
  expect(await response.json()).toEqual({ error: "unauthorized" });
});

test("logout clears the session cookie", async ({ context, page, request }) => {
  const validKey = await resolveValidKey(request);

  await page.goto("/login");
  await page.getByTestId("login-key").fill(validKey);
  await page.getByTestId("login-submit").click();
  await page.waitForURL("**/whoami");

  await page.getByTestId("logout-submit").click();
  await page.waitForURL("**/login");

  const cookies = await context.cookies();
  expect(cookies.some((cookie) => cookie.name === sessionCookieName)).toBe(false);
});
