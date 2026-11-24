# Vendored front-end libraries

Served by WhiteNoise from the application origin (no CDN), so pages work
offline and a future `script-src 'self'` Content Security Policy stays viable.
Both files are loaded by `conformance/api/templates/conformance/partials/htmx_script.html`.

| File | Version | Licence | Source (npm tarball, file) | SHA-256 |
|------|---------|---------|----------------------------|---------|
| `htmx-2.0.11.min.js` | 2.0.11 | 0BSD | `https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz`, `package/dist/htmx.min.js` | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `htmx-ext-head-support-2.0.5.min.js` | 2.0.5 | 0BSD | `https://registry.npmjs.org/htmx-ext-head-support/-/htmx-ext-head-support-2.0.5.tgz`, `package/dist/head-support.min.js` | `86e59ae1048bf02fb31a5842b4b559a1058b4c3ff190c258530ecb94f9fa6d56` |

npm tarball integrity values:

- `htmx.org@2.0.11`: `sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==`
- `htmx-ext-head-support@2.0.5`: `sha512-fTzqnCw7OnMijiS48CCBFBHTuOMvycbX0nEFRL0lkRxyu5DFWX90IV1SwXpQ92fJf02IzkXcRNyjXn3j0QtYxA==`

## Upgrading

1. Download the new tarball from the npm registry and check it against the
   registry's `dist.integrity` value.
2. Copy the minified file here with the version in the filename (this busts
   browser caches) and delete the old file.
3. Update the `{% static %}` paths in `partials/htmx_script.html`, this README
   (version, source and SHA-256), and the asset test in
   `tests/component/django/test_htmx_frontend.py`.
