# Denver apartment search

Friend-facing self-serve rental search for the Cherry Creek 10-minute ring.
Deployed on Render as an always-on web service.

## What it is
A small Python (stdlib) web server. A friend opens the URL, sets budget / beds /
baths / garage / home type / depth, and it sweeps Craigslist, Zillow, the big
portals, institutional landlords, and by-owner sites, then serves a digest.

## Deploy (Render)
1. New → Web Service → connect this repo.
2. Render reads `render.yaml`. Set the two secrets when prompted:
   - `EXA_API_KEY`
   - `FIRECRAWL_API_KEY`
3. Deploy. The public URL Render gives you is what you share.

## Notes
- Source of truth is `~/Documents/AI-OS/projects/apartment-hunt`. This repo is the
  deploy copy; re-copy the 5 files after changes there.
- The Reddit source (`reddit-cli`) needs a local browser cookie and is skipped in
  the cloud — non-fatal, that channel just returns nothing.
- Firecrawl credits are the real limit: a full search burns ~220. Plan resets
  monthly. Prefer the "quick sweep" depth to conserve.
- Single instance only — it holds run state in memory and runs one search at a
  time. Do not scale past 1.
