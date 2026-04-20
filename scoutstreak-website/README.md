# ScoutStreak Website

Public marketing site for [scoutstreak.com](https://scoutstreak.com). Built with Astro 6, Tailwind CSS v4, and zero client-side frameworks.

## Development

```bash
npm install
npm run dev        # starts at http://localhost:4321
```

## Build

```bash
npm run build      # outputs static files to dist/
npm run preview    # preview the built site locally
```

## Deploy to Cloudflare Pages

1. Connect the repo to Cloudflare Pages (dashboard.cloudflare.com > Pages > Create)
2. Set build command: `npm run build`
3. Set build output directory: `dist`
4. Set Node.js version: `20` (environment variable `NODE_VERSION=20`)
5. Deploy

Or deploy manually:

```bash
npm run build
npx wrangler pages deploy dist --project-name=scoutstreak
```

## Before first production deploy

- Convert `public/og-image.svg` to a 1200x630 PNG at `public/og-image.png` and update `src/layouts/Base.astro` references
- Verify the launch signup endpoint at `https://scoutstreak-api.fly.dev/api/public/launch-signup` is live (currently mocked in dev mode)

## Architecture

- `src/layouts/Base.astro` — HTML shell with meta, fonts, View Transitions
- `src/layouts/Legal.astro` — Shared layout for /privacy and /terms
- `src/components/` — Reusable Astro primitives (Eyebrow, SectionHeadline, CTAButton, KpiChip, RoleBar, FeatureCard, PricingCard)
- `src/components/sections/` — One component per homepage section
- `src/pages/` — index.astro, privacy.astro, terms.astro
- `public/` — Static assets (favicon, OG image, robots.txt, sitemap.xml)
