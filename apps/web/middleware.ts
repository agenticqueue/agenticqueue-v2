import { getRequestSession } from "@/app/lib/session";
import { bearerHeaders, upstreamUrl } from "@/app/lib/upstream";
import { NextRequest, NextResponse } from "next/server";

const LOGIN_FAILURE_HTML = `<!doctype html><html lang="en"><body><main><h1>Unauthorized</h1></main></body></html>`;

function loginFailure(): Response {
  return new Response(LOGIN_FAILURE_HTML, {
    status: 401,
    headers: {
      "content-type": "text/html; charset=utf-8",
    },
  });
}

function sameHostUrl(request: NextRequest, path: string): URL {
  const forwardedHost = request.headers.get("x-forwarded-host");
  const forwardedProto = request.headers.get("x-forwarded-proto");
  const host = forwardedHost ?? request.headers.get("host") ?? request.nextUrl.host;
  const protocol = forwardedProto ?? request.nextUrl.protocol.replace(/:$/, "");
  return new URL(path, `${protocol}://${host}`);
}

async function formKey(request: NextRequest): Promise<string | null> {
  try {
    const formData = await request.formData();
    const value = formData.get("key");
    if (typeof value !== "string") {
      return null;
    }
    const key = value.trim();
    return key.length > 0 ? key : null;
  } catch {
    return null;
  }
}

async function bearerIsValid(apiKey: string): Promise<boolean> {
  try {
    const response = await fetch(upstreamUrl("/actors/me"), {
      cache: "no-store",
      headers: bearerHeaders(apiKey),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest): Promise<Response> {
  if (request.method !== "POST") {
    return NextResponse.next();
  }

  const apiKey = await formKey(request);
  if (!apiKey || !(await bearerIsValid(apiKey))) {
    return loginFailure();
  }

  const response = NextResponse.redirect(sameHostUrl(request, "/whoami"), 302);
  const session = await getRequestSession(request, response);
  session.apiKey = apiKey;
  await session.save();
  return response;
}

export const config = {
  matcher: "/login",
};
