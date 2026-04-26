import { headers } from "next/headers";
import type { components } from "@/app/types/api";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type HealthStatus = components["schemas"]["HealthStatus"];
type VersionInfo = components["schemas"]["VersionInfo"];

export const dynamic = "force-dynamic";

async function fetchSurface<T>(origin: string, path: string): Promise<T> {
  const response = await fetch(`${origin}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }
  return (await response.json()) as T;
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

export default async function Home() {
  const headerStore = await headers();
  const host = headerStore.get("x-forwarded-host") ?? headerStore.get("host");
  const protocol = headerStore.get("x-forwarded-proto") ?? "http";
  const origin = `${protocol}://${host ?? "localhost:3000"}`;

  const [health, version] = await Promise.all([
    fetchSurface<HealthStatus>(origin, "/api/health"),
    fetchSurface<VersionInfo>(origin, "/api/version"),
  ]);

  return (
    <main className="min-h-screen bg-background">
      <section className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-10 md:py-14">
        <header className="flex flex-col gap-3">
          <Badge className="w-fit" variant="secondary">
            AgenticQueue 2.0
          </Badge>
          <div className="flex flex-col gap-2">
            <h1 className="text-3xl font-semibold tracking-normal md:text-4xl">
              Four-surface ping
            </h1>
          </div>
        </header>

        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>HealthStatus</CardTitle>
              <CardDescription>GET /healthz</CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="flex flex-col gap-3">
                <Field
                  label="status"
                  testId="health-status"
                  value={health.status}
                />
                <Field
                  label="timestamp"
                  testId="health-timestamp"
                  value={health.timestamp}
                />
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>VersionInfo</CardTitle>
              <CardDescription>GET /version</CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="flex flex-col gap-3">
                <Field
                  label="version"
                  testId="version-version"
                  value={version.version}
                />
                <Field
                  label="commit"
                  testId="version-commit"
                  value={version.commit}
                />
                <Field
                  label="built_at"
                  testId="version-built-at"
                  value={version.built_at}
                />
              </dl>
            </CardContent>
          </Card>
        </div>
      </section>
    </main>
  );
}
