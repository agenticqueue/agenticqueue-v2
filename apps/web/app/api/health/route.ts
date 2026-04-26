export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const DEFAULT_API_URL = "http://api:8000";

function upstreamUrl(path: string): string {
  return `${(process.env.AQ_API_URL ?? DEFAULT_API_URL).replace(/\/$/, "")}${path}`;
}

export async function GET(): Promise<Response> {
  const url = upstreamUrl("/healthz");
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return Response.json(
        {
          error: "upstream_error",
          surface: "health",
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
        surface: "health",
        message: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }
}
