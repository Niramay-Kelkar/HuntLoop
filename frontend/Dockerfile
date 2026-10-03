# Multi-stage build for the Next.js frontend - a production-style
# `next build` + `next start`, not the dev server. Hot reload inside a
# container isn't useful for what this solves (a stranger's first run
# via `docker compose up`, no Node/npm install required locally); the
# existing non-containerized `npm run dev` workflow (see README.md's
# Frontend section) is unaffected and stays the faster loop for active
# development.
#
# Base image: node/package.json declares no "engines" field, but
# node_modules/next/package.json pins "engines": {"node": ">=20.9.0"}
# (Next 16.3.2) - node:24-alpine is the current Active LTS line as of
# this change and satisfies that floor.

# --- deps: install once, cached across builds unless package*.json change ---
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- builder: full source + deps, produces the production .next build ---
FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_-prefixed vars are inlined into the client bundle by
# `next build` itself (see src/lib/api.ts's comment) - unlike the other
# services in docker-compose.yml, this can't be a plain runtime
# `environment:` entry, it has to be a build ARG so it's baked in before
# `next start` ever runs. Defaults to the same http://localhost:8000
# src/lib/api.ts already falls back to, so an unset build-arg still
# produces a working image.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
# Clerk's own publishable key (see src/app/layout.tsx's ClerkProvider,
# src/proxy.ts) - same NEXT_PUBLIC_-inlined-at-build-time reasoning as
# NEXT_PUBLIC_API_URL above. An empty value (the default - not every
# deployment of this repo needs the admin UI) builds a working image;
# ClerkProvider itself surfaces a clear client-side error if a real key
# was actually needed and wasn't provided.
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=""
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY}
RUN npm run build

# --- runner: prod-only deps + build output, non-root, minimal surface ---
FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

RUN addgroup --system huntloop && adduser --system --ingroup huntloop huntloop

COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./next.config.ts

RUN chown -R huntloop:huntloop /app
USER huntloop

EXPOSE 3000
CMD ["npm", "start"]
