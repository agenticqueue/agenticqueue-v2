import { getIronSession, type IronSession, type SessionOptions } from "iron-session";

export const SESSION_COOKIE_NAME = "aq2_session";
export const SESSION_TTL_SECONDS = 8 * 60 * 60;

export type WebSessionData = {
  apiKey?: string;
};

function requireSessionSecret(): string {
  const secret = process.env.AQ_SESSION_SECRET;
  if (!secret || secret.length < 32) {
    throw new Error("AQ_SESSION_SECRET must be set and at least 32 characters.");
  }
  return secret;
}

export function sessionOptions(): SessionOptions {
  return {
    cookieName: SESSION_COOKIE_NAME,
    password: requireSessionSecret(),
    ttl: SESSION_TTL_SECONDS,
    cookieOptions: {
      httpOnly: true,
      secure: process.env.AQ_COOKIE_SECURE === "true",
      sameSite: "strict",
      maxAge: SESSION_TTL_SECONDS,
      path: "/",
    },
  };
}

export async function getCookieStoreSession(): Promise<IronSession<WebSessionData>> {
  const { cookies } = await import("next/headers");
  return getIronSession<WebSessionData>(await cookies(), sessionOptions());
}

export async function getRequestSession(
  request: Request,
  response: Response,
): Promise<IronSession<WebSessionData>> {
  return getIronSession<WebSessionData>(request, response, sessionOptions());
}
