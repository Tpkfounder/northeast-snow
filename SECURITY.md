# Security policy

Northeast Snow is a static website: no accounts, no cookies, no user data, no server-side code. The data is rebuilt by a GitHub Action that stores no secrets.

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

- Use **[Report a vulnerability](https://github.com/Tpkfounder/northeast-snow/security/advisories/new)** on this repository's Security tab.

Helpful details: what you found, how to reproduce it, and what an attacker could do with it. You'll get an acknowledgement as soon as possible, and fixes go live on the next deploy.

## In scope

- The website (`index.html`, `app.js`) and what it loads
- The GitHub Actions workflow and the data pipeline scripts

## Out of scope

- The upstream data services (NWS, NOAA, avalanche.org, OpenStreetMap and others): report issues to them directly
- Forecast accuracy. That isn't a security issue; please open a regular issue instead.

## What's already in place

- Strict Content-Security-Policy; no inline scripts; the Leaflet map library is pinned with a Subresource Integrity hash
- All data is HTML-escaped before it's shown; only `https://` links are allowed
- The workflow has no default permissions, uses SHA-pinned actions, doesn't persist git credentials, and installs prebuilt wheels only
- Dependabot checks GitHub Actions and Python packages monthly
