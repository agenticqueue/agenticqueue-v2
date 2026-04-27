import { bearerHeaders, upstreamUrl } from "@/app/lib/upstream";
import { getCookieStoreSession } from "@/app/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UNAUTHORIZED_BODY = { error: "unauthorized" };

function unauthorized(): Response {
  return Response.json(UNAUTHORIZED_BODY, { status: 401 });
}

export async function GET(): Promise<Response> {
  const session = await getCookieStoreSession();
  if (!session.apiKey) {
    return unauthorized();
  }

  try {
    const response = await fetch(upstreamUrl("/actors/me"), {
      cache: "no-store",
      headers: bearerHeaders(session.apiKey),
    });
    if (response.status === 401) {
      return unauthorized();
    }
    if (!response.ok) {
      return Response.json(
        {
          error: "upstream_error",
          surface: "actors/me",
          status_code: response.status,
        },
        { status: 502 },
      );
    }
    return new Response(await response.text(), {
      status: 200,
      headers: {
        "content-type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    return Response.json(
      {
        error: "upstream_error",
        surface: "actors/me",
        message: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }
}
