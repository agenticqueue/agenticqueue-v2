import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default function LoginPage() {
  return (
    <main className="min-h-screen bg-background">
      <section className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center gap-6 px-6 py-10">
        <header className="flex flex-col gap-3">
          <Badge className="w-fit" variant="secondary">
            AgenticQueue 2.0
          </Badge>
          <h1 className="text-3xl font-semibold tracking-normal">Sign in</h1>
        </header>

        <Card>
          <CardHeader>
            <CardTitle>Actor session</CardTitle>
            <CardDescription>Bearer key authentication</CardDescription>
          </CardHeader>
          <CardContent>
            <form action="/login" className="flex flex-col gap-4" method="post">
              <label className="flex flex-col gap-2 text-sm font-medium">
                API key
                <input
                  autoComplete="off"
                  className="h-10 rounded-md border border-input bg-background px-3 py-2 font-mono text-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  data-testid="login-key"
                  name="key"
                  required
                  type="password"
                />
              </label>
              <button
                className="h-10 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                data-testid="login-submit"
                type="submit"
              >
                Sign in
              </button>
            </form>
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
