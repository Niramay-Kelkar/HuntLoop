This is the HuntLoop frontend — a Next.js (App Router) app originally
bootstrapped with `create-next-app`, but since built out into HuntLoop's
real UI (dashboard, job list/detail, application tracker, resume
management, a BYOK chat/drafting assistant). **This file is unmodified
`create-next-app` boilerplate — see the top-level `../README.md`'s
"Frontend" section and `../CLAUDE.md`'s frontend bullet for the real setup
instructions, page structure, and visual identity**; a few of the generic
claims below are already wrong for this project specifically (noted
inline) and shouldn't be trusted over those two files.

## Getting Started

First, run the development server:

```bash
npm install
npm run dev
```

(`yarn`/`pnpm`/`bun` are not used in this project — see `package-lock.json`.)

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result. The API (`../src/huntloop/api/`) must already be running — see the top-level README's "API service" section.

You can start editing the app by modifying files under `src/app/` (not
`app/` — this project's App Router lives under `src/`). The page
auto-updates as you edit the file.

**This project does NOT use Geist** (the `create-next-app` default this
paragraph originally described) — it uses Public Sans (body/UI text) and
IBM Plex Mono (all numeric/tabular figures), both loaded via
`next/font/google`. See the top-level `../CLAUDE.md`'s "VISUAL IDENTITY"
note for the full reasoning (the app runs on U.S. federal DOL filing data,
hence Public Sans).

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

## Deployment

This project is deployed via Docker (`frontend/Dockerfile` + the
`frontend` service in the top-level `docker-compose.yml`), not Vercel —
see the top-level README's "Run with Docker" and "Frontend" sections.
