FROM node:20-alpine AS deps

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json

RUN pnpm install --frozen-lockfile

FROM deps AS builder

COPY tests/parity/openapi.snapshot.json ./tests/parity/openapi.snapshot.json
COPY apps/web ./apps/web

RUN pnpm --filter @agenticqueue/web gen:types \
    && pnpm --filter @agenticqueue/web build

FROM node:20-alpine AS runtime

ENV NODE_ENV=production

WORKDIR /app

RUN corepack enable

COPY --from=builder /app ./

EXPOSE 3000

CMD ["pnpm", "--filter", "@agenticqueue/web", "start"]
