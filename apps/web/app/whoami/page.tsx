import { getCookieStoreSession } from "@/app/lib/session";
import { bearerHeaders, upstreamUrl } from "@/app/lib/upstream";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { redirect } from "next/navigation";

type WhoamiResponse = {
  actor: {
    id: string;
    name: string;
    kind: "human" | "agent" | "script" | "routine";
    created_at: string;
    deactivated_at: string | null;
  };
};

export const dynamic = "force-dynamic";

async function fetchWhoami(apiKey: string): Promise<WhoamiResponse> {
  const response = await fetch(upstreamUrl("/actors/me"), {
    cache: "no-store",
    headers: bearerHeaders(apiKey),
  });
  if (response.status === 401) {
    redirect("/login");
  }
  if (!response.ok) {
    throw new Error(`Failed to load actor identity: ${response.status}`);
  }
  return (await response.json()) as WhoamiResponse;
}

function Field({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border bg-muted/40 px-3 py-2">
      <dt className="text-xs font-medium uppercase tracking-normal text-muted-foreground">
        {label}
      </dt>
      <dd className="break-all font-mono text-sm" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

export default async function WhoamiPage() {
  const session = await getCookieStoreSession();
  if (!session.apiKey) {
    redirect("/login");
  }

  const { actor } = await fetchWhoami(session.apiKey);

  return (
    <main className="min-h-screen bg-background">
      <section className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-10 md:py-14">
        <header className="flex flex-col gap-3">
          <Badge className="w-fit" variant="secondary">
            AgenticQueue 2.0
          </Badge>
          <div className="flex flex-col gap-2">
            <h1 className="text-3xl font-semibold tracking-normal md:text-4xl">
              Whoami
            </h1>
          </div>
        </header>

        <Card>
          <CardHeader>
            <CardTitle>Authenticated actor</CardTitle>
            <CardDescription>GET /actors/me</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 md:grid-cols-2">
              <Field label="id" testId="whoami-actor-id" value={actor.id} />
              <Field label="name" testId="whoami-actor-name" value={actor.name} />
              <Field label="kind" testId="whoami-actor-kind" value={actor.kind} />
              <Field
                label="created_at"
                testId="whoami-actor-created-at"
                value={actor.created_at}
              />
              <Field
                label="deactivated_at"
                testId="whoami-actor-deactivated-at"
                value={actor.deactivated_at ?? ""}
              />
            </dl>
          </CardContent>
        </Card>

        <form action="/logout" method="post">
          <button
            className="h-10 rounded-md border border-input bg-background px-4 py-2 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="logout-submit"
            type="submit"
          >
            Sign out
          </button>
        </form>
      </section>
    </main>
  );
}
