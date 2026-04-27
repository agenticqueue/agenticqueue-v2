import { getRequestSession } from "@/app/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const response = new Response(null, {
    status: 302,
    headers: {
      location: "/login",
    },
  });
  const session = await getRequestSession(request, response);
  session.destroy();
  return response;
}
