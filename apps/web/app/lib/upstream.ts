export const DEFAULT_API_URL = "http://api:8000";

export function upstreamUrl(path: string): string {
  return `${(process.env.AQ_API_URL ?? DEFAULT_API_URL).replace(/\/$/, "")}${path}`;
}

export function bearerHeaders(apiKey: string): HeadersInit {
  return {
    authorization: `Bearer ${apiKey}`,
  };
}
