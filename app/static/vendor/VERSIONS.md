# Vendored Frontend Assets & Integrity Signatures

All third-party client JavaScript libraries used by FDR-Sentinel are vendored locally in this directory to guarantee air-gapped evaluation, zero CDN latency, deterministic behavior, and CSP adherence.

| Asset Filename | Package | Version | Source URL | SHA-256 Hash |
| :--- | :--- | :--- | :--- | :--- |
| `chart.umd.min.js` | Chart.js (UMD) | 4.4.7 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js` | `206b6e8bb00fc7bba2c7ee80ca41db3e9e05ba7be0aa35abeba9cfd5357f5d0e` |
| `htmx.min.js` | HTMX | 2.0.4 | `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` | `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452` |
| `tailwind.min.js` | Tailwind CSS Play CDN | 3.4.1 | `https://cdn.tailwindcss.com/3.4.1` | `176e894661aa9cdc9a5cba6c720044cbbf7b8bd80d1c9a142a7c24b1b6c50d15` |
