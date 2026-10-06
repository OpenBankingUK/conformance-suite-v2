# Functional Conformance Suite v2 beta.8

**Beta only — not for certification.** FCS v2 `2.0.0-beta.8` is an MVP for
evaluating the new Open Banking UK Functional Conformance Suite and providing
feedback. It is **not approved for certification**; runs and reports from this
beta must not be submitted as certification evidence, even if a plan or result
mentions certification or appears eligible in the UI.

This applies to every way of running this version — the published Docker image,
a locally built image, and a run from source are all equally non-certifying.

**Keep using the existing FCS v1 for certification.** Nothing about your
certification process changes while this beta is available. Continue to run
your certification tests on FCS v1 and submit those results as usual. Results
produced here are for evaluation and feedback only, and Open Banking UK will
not accept them as certification submissions. Certification on FCS v2 will only
begin once a later release is formally approved for it, and that will be
announced separately — you do not need to migrate or do anything in advance.

## Get started: local browser UI

With Docker installed and running, start the beta on your own computer:

```bash
docker run --pull=always --rm -p 127.0.0.1:8443:8443 openbanking/conformance-suite-v2:2.0.0-beta-latest
```

`2.0.0-beta-latest` tracks the newest published 2.0.0 beta. `--pull=always`
refreshes the image each time you start a container; it does not update an
already running container. This tag is for evaluation only, not certification.

Open `https://127.0.0.1:8443/` in your browser. Accept the warning for the
container's locally generated self-signed HTTPS certificate. The Docker
command makes the UI accessible only from your computer.

Choose **Create new test plan with builder**, follow the prompts, and paste
the credentials required by your chosen test plan into the browser's
**Paste PEM text** fields (select **Paste or upload** as the credential
source). You do not need to mount a certificate directory or create a JSON
test plan to try the UI. Review the generated plan and choose **Launch run**;
some tests also need runtime inputs or a PSU authorisation step. Save any
results you need before stopping the container with Ctrl+C: this command
does not persist browser sessions, results, or logs across runs.

For the complete beta UI walkthrough and optional persistence or certificate
mounts, see the [Docker deployment guide](docs/DOCKER_GUIDE.md). CLI, REST,
Compose, and file-based credential configuration below are advanced reference,
not the primary beta.8 participant workflow.

## Give beta feedback

Choose **Give beta feedback** in the beta banner on any browser page. It opens
a new tab without interrupting run monitoring, PSU actions or builder edits.
Select **Bug**, **Suggestion**, **Issue** or **Other**, describe what happened,
and choose **Prepare message and diagnostic ZIP**. The recipient is
`standardsteam@openbanking.org.uk`.

Nothing is uploaded or sent automatically. Download and review the ZIP, copy the
complete subject/body, and **manually attach the ZIP** to your email. The optional
**Open email client** link creates only a short report reference; email links cannot attach
files reliably. No mail client is required: selectable text and `feedback.txt`
work without JavaScript. On VMs or secured networks, transfer reviewed files
using your organisation's approved process and send via webmail or another
device; do not bypass data-sharing or network restrictions.

Each bundle includes a versioned `context.json` (tool version, source page URL
without query parameters/fragments, capture time and supplied environment notes),
`manifest.json` (inventory, masking policy and unavailable-evidence reasons), and
`feedback.txt`. Run pages add status, execution logs, results, the launch-time
test plan and validation when available. Active runs contain a point-in-time
snapshot, not future results. Saved builder drafts add a canonical safe test plan
when convertible, otherwise a clearly labelled `builder-draft.json` with the
conversion error. **Unsaved browser edits are not captured**, and a diagnostic
test plan is not a guarantee of executable configuration. Page-only feedback
does not attach another run or draft.

The feedback form asks for a category, summary and description, with optional
reproduction steps and environment notes. Sharing warnings and attachment
instructions stay on the feedback page; the email contains the feedback and
diagnostic context without those instructions or empty optional sections.

Credential/certificate masking is mandatory even for developer-mode evidence.
It covers recognized credential fields, HTTP authorization/cookie headers, PEM,
JWT/JWS/JWE, credential-bearing URL parameters/userinfo and recognized free-text
credential forms. **Identifiers, URLs and local file paths are retained**.
Arbitrary narrative or business data may still be sensitive: review every file
before sharing. Bundles never read credential files or collect sessions, request
cookies, environment variables or host/container logs.

Prepared reports are session-owned, process-local and expire after 30 minutes.
The tool retains at most three per browser session and twenty globally, with
a 64 MiB stored-ZIP limit; oldest reports may be evicted sooner. Each bundle is
limited to 16 MiB of uncompressed content and fails explicitly if too large.
Reports are lost on server restart. Download promptly; if a report becomes
unavailable, prepare a new one. Multi-worker deployments require sticky routing
to the process holding both the run and report, like the existing in-memory run
store.

## Alternative: run from source

If you would rather review and run the source code than pull the published
image, you can start the same browser UI locally. This is an alternative to
Docker, not an additional requirement, and it produces the same non-certifying
beta behaviour.

You need:

- Python 3.14.4 or later (the version is pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- GNU Make and OpenSSL (both are present by default on macOS and most Linux
  distributions)

```bash
git clone https://github.com/OpenBankingUK/conformance-suite-v2.git
cd conformance-suite-v2
uv sync --frozen --no-install-project
make dev
```

`make dev` generates a local self-signed certificate under
`local-config/certs/` on first use and serves the application over HTTPS. Open
`https://127.0.0.1:8443/` and accept the certificate warning, then follow the
same browser builder workflow described below. Stop the server with Ctrl+C.

There is no database to migrate and no separate build step. If you prefer to
run without auto-reload and with Django debug mode off, use `make serve`
instead. To build and run the hardened container image from the same checkout,
use `make docker`.

If you intend to change the code as well as run it, see the
[Developer Guide](docs/DEVELOPER_GUIDE.md) for the full toolchain, the
`make check` verification workflow, and environment variable reference.

## Browser builder workflow

Participants no longer select checked-in suites, manifests, or config examples.
The supported workflow is:

1. Open the browser main menu at `/`.
2. Choose **Create a new test plan with builder** or **Import test plan**.
3. **Specification:** select the scheme, specification, and version. For
   Read/Write, the latest OpenAPI document update for that version is
   preselected; choose an earlier one only if you need it.
4. **Scope:** for Read/Write, tick the resource groups you implement. Ticking a
   group selects all of its endpoints and optional features. Mandatory
   endpoints and required features stay locked, and **Deselect conditional
   and optional endpoints and features** removes only the ones you can opt
   out of. For
   Dynamic Client Registration 3.4, select direct endpoints: POST is always
   selected and locked; GET, PUT, and DELETE are optional. DCR token traffic is
   generated and is never a participant-selected endpoint.
5. **Connection & security:** enter the OpenID discovery URL and choose
   **Fetch and fill** to fill empty OAuth/FAPI fields from the discovery
   metadata (values you typed are kept unless you choose **Replace with
   discovery values**). Each field shows **Required to run**, **Optional**, or
   **Depends on scope** for the selected endpoints. For required credentials,
   select **Paste or upload** and use **Paste PEM text**; do not put host file
   paths in the container's **Absolute file path** field.
6. **Business data:** provide resource-group-specific business data. Fields
   appear only for the selected scope, with the specification's **Required**
   badges.
7. **Review:** check the generated schemaVersion `1.0` test plan and launch the
   run. Review is the single validation gate: it lists each step's issues with
   a **Fix** button, and **Launch run** stays blocked until they are resolved.
   The plan JSON box is editable and syntax-highlighted; edits (or a file from
   **Load from file…**) are applied automatically after a short pause, and a
   JSON syntax error is reported with its line number and underlined on that
   line. **Export safe JSON** omits secret values, so you will need to enter
   them again when importing it. **Export with secrets** contains sensitive
   material; use only if necessary and store it securely.

The step bar at the top of every builder page shows every step. Once a
specification is chosen, you can jump to any step in any order; leaving a page
always saves what you entered, even if it is incomplete. Steps are marked
complete (✓) or needing attention (!). **Import plan**, beside **Main menu** on
every builder page, loads a test plan JSON file or pasted JSON into the current
draft (after a **Replace current plan?** confirmation if the draft already has
values). After a run, **Main menu** on the run screen returns you to the choice
between building and importing a plan.

The UI shows generated tests, counts, source traceability, runtime/auth
requirements, launch blockers, and internal certification-status labels after
preview. **Those labels do not make beta.8 runs valid for certification.**
Generated tests are read-only: participants cannot select exact generated
tests. Lower-level request and assertion details stay collapsed under audit
details.

## Advanced reference: CLI plan execution

For advanced CLI use, the runner accepts a canonical JSON-first test plan that contains
the specification, security environment, specification-owned scope/config, and
reporting metadata in one portable document:

```bash
uv run python main.py --test-plan path/to/test-plan.json
```

Browser discovery can prefill OAuth/FAPI values from OpenID metadata, but
exported JSON includes only the final accepted values without recording whether
they came from discovery or manual entry. A single Read/Write plan can span AIS,
PIS, CBPII, and VRP catalogue areas when those groups use one security
environment and OpenID discovery URL. cVRP is not exposed under the Open Banking
UK Read/Write boundary for now. Read/Write version `3.1.11` is backed by
dedicated v3.1 catalogue areas; it is not routed through the v4 catalogues.

### OpenAPI document updates

Open Banking UK Read/Write core specification pages only change with a version
bump (for example `4.0.0` → `4.0.1`), but the OpenAPI ("swagger") documents that
represent a version are republished upstream as updates. Read/Write plans must
select one with `specification.openApiDocumentUpdate`; response-schema
assertions then validate against that update's pinned snapshot. Names follow the
upstream history: `Baseline` is the original publication, and later updates
keep the terminology in use when they were published (`Release-N` before the
switch to `Update-N`). **Import test plan** loads older plans that omit the
field, selects the latest update and shows a warning so you can change it;
REST and CLI runs reject them.

| Version | `openApiDocumentUpdate` values (oldest → latest) |
| --- | --- |
| `3.1.11` | `Baseline`, `Release-2`, `Release-3`, `Release-4`, `Release-5` |
| `4.0.0` | `Baseline`, `Release-2`, `Update-3`, `Update-4`, `Update-5` |
| `4.0.1` | `Baseline`, `Update-1` |

The browser builder preselects the latest update and lets you choose a
historical one. DCR plans must not set `openApiDocumentUpdate`. Results record
the selected update (with its upstream tag and commit) under
`catalogue.openApiDocumentUpdate`, alongside `specificationVersion` and
`endpointVersion`.

For v3.1.11 domestic standing orders, set
`businessTestData.pis.standingOrderFrequencyV31` to the scalar frequency format
defined by the v3.1 specification, such as `EvryDay` or
`IntrvlWkDay:01:03`. The v4 `standingOrderFrequency` object remains unchanged
for v4 plans.

```json
{
  "schemaVersion": "1.0",
  "specification": {
    "family": "OBL_READ_WRITE",
    "version": "4.0.1",
    "openApiDocumentUpdate": "Update-1",
    "profile": "FAPI1_ADVANCED"
  },
  "executionMode": "certification",
  "securityEnvironment": {
    "name": "Primary Authorization Server",
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
    "clientAuthMethod": "private_key_jwt",
    "signingAlgorithm": "PS256",
    "resourceBaseUrl": "https://resource.example.com",
    "mtls": {
      "enabled": true,
      "certificatePath": "/absolute/path/to/transport-cert.pem"
    }
  },
  "resourceGroups": ["AIS"],
  "businessTestData": {
    "ais": {"accountIds": ["account-123"]},
    "inputs": {"accessToken": {"value": "token-reference-or-local-debug-value"}}
  },
  "metadata": {
    "aspspName": "Example Bank",
    "brandName": "Example Retail",
    "environmentName": "Sandbox"
  }
}
```

`resourceGroups` accepts either shorthand group names such as `"AIS"` or detailed
objects with explicit endpoint/capability selections for builder exports. Required
endpoint capabilities may be omitted because the compiler selects them
automatically for implemented endpoints. Optional capabilities must be listed
under their endpoint to generate implementation-dependent tests. Public browser,
CLI, and REST execution paths accept canonical schemaVersion `1.0` plans only.
`config.testSuite`, public `--manifest`, public `--deselect`, public
`--plan-spec`, REST `manifest`, REST `planSpec`, and REST `deselectStepIds` are
intentionally rejected. Mandatory applicable catalogue tests cannot be
arbitrarily deselected.

Specification endpoint implementation requirements are separate from required
tests for implemented endpoints. The
[Read/Write endpoint requirement matrix](docs/READ_WRITE_ENDPOINT_REQUIREMENTS.md)
records Mandatory, Conditional and Optional classifications from the individual
resource pages for v4.0.1, v4.0.0 and v3.1.11, including dependencies and source
discrepancies.

DCR plans instead use family `OBL_DCR`, specification
`dynamic-client-registration`, version `3.4`, top-level `endpoints`, and
`dynamicClientRegistration`; they must not contain `resourceGroups` or
`businessTestData`. See
[`docs/DCR_3_4_PARITY_CONTRACT.md`](docs/DCR_3_4_PARITY_CONTRACT.md) for the
complete canonical example, configuration migration, supported auth methods, and
operator workflow. Legacy `conformance-dcr` JSON is not directly importable.

## Browser and REST launch

The browser wizard imports, exports, reviews, and launches the same canonical
test plan that the catalogue compiler accepts through CLI and REST. The REST run
creation endpoint accepts the canonical document directly, or under `testPlan`,
in `POST /api/runs/`:

```json
{
  "schemaVersion": "1.0",
  "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "openApiDocumentUpdate": "Update-1"},
  "securityEnvironment": {
    "discoveryUrl": "https://aspsp.example.com/.well-known/openid-configuration",
    "resourceBaseUrl": "https://resource.example.com"
  },
  "resourceGroups": ["AIS"],
  "businessTestData": {},
  "metadata": {}
}
```

Browser exports are secret-safe by default: the generated schemaVersion `1.0`
test plan preserves resource-group, endpoint, capability, business-data, and
non-sensitive runtime references, but writes secret-bearing strings as empty strings. A separate
export-with-secrets action is available for local power-user workflows. Launch
still uses Read/Write runtime values retained in the same browser session or
supplied by direct CLI/API submission. DCR plans can use inline credentials in
the builder for a local run; safe exports omit their sensitive values. Handle
any export containing secrets as confidential.

Run detail, result downloads, and NDJSON execution logs keep the existing
masking and evidence behaviour. Result JSON includes the safe test-plan snapshot,
the shared validation outcome, catalogue traceability for selected endpoints,
selected capabilities, generated test-case IDs, applicability decisions, runtime
input snapshots with sensitive values omitted, and non-certifying reasons. Manual
PSU authorisation handoff URLs remain transient browser state; persisted
artifacts mask credentials, tokens, request objects, client assertions, detached
JWS values, and sensitive headers.

DCR results additionally contain ordered scenario → case → step trace groups.
Unselected optional operations are explicit `skipped` cases/steps with
`endpoint-not-selected`; prerequisite failures skip dependent runtime steps and
fail the aggregate run. Automation must use
`python -m conformance.result_gate out/test-results.json`, which rejects any
failed structured case or step regardless of console transcript text.

## Bundled catalogues

The bundled catalogue registry currently covers the legacy FCS baseline for:

| Standard | Endpoint version | Specification version | API family |
| --- | --- | --- | --- |
| `open-banking` | `v3.1` | `3.1.11` | `ais`, `pis`, `cbpii`, `vrp` |
| `open-banking` | `v4.0` | `4.0.0` | `ais`, `pis`, `cbpii`, `vrp` |
| `open-banking` | `v4.0` | `4.0.1` | `ais`, `pis`, `cbpii`, `vrp` |
| `open-banking` | `v3.4` | `3.4` | `dcr` |

The participant-facing Read/Write versions are `3.1.11`, `4.0.0`, and `4.0.1`.
`4.0.0` and `4.0.1` share `v4.0` endpoint paths but have independent catalogue
copies so their coverage can diverge.
Each catalogue case carries traceability back to the relevant legacy FCS
coverage in its compliance scope. Each catalogue can also define endpoint-scoped
capabilities that explain baseline and optional implementation coverage without
turning generated tests into participant selections. The hand-maintained mapping
lives in `docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.

Dynamic Client Registration 3.4 is first-class across browser, CLI, and local
REST execution. It has no resource-group page or resource-server/business-data
configuration.

## Outputs and exit codes

The runner writes a structured result JSON to `resultOutputPath`, defaulting to
`out/test-results.json`, and writes an NDJSON execution log to
`executionLogPath`, defaulting to `out/execution-log.ndjson`.

CLI exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | All selected checks passed. |
| `1` | Execution completed with failed checks. |
| `2` | Config, canonical test plan, or catalogue compilation input was invalid. |
| `3` | Result or execution-log output could not be written. |

Set `CONFORMANCE_DEVELOPER_MODE=true` only for local debugging. It disables
masking in developer-visible logs and must never be enabled in release builds.

## Internal certification report validation (not for beta submissions)

The OBL-side certification validator remains an internal reviewer tool. It
validates a submitted result report against the manifest representation used for
the original run and an independently supplied approved-release policy:

```bash
uv run python -m conformance.certification_cli out/test-results.json \
  --manifest path/to/internal-manifest.json \
  --approved-releases path/to/approved-releases.json
```

Approved-release policy files use this shape:

```json
{
  "schemaVersion": "v1",
  "approvedToolVersions": ["OBL-APPROVED-RELEASE-VERSION"]
}
```

Generated reports include catalogue traceability, runtime input snapshots with
sensitive values omitted, certification/non-certification reasons, and stable
`metadata.reportVersion` plus `tool.version` fields consumed by the validator.

### Phase 1 assurance boundary

A passing validator result means that the submitted report is consistent with
the independently supplied mandatory-test criteria and approved-release policy.
It is certification-ready evidence for OBL review, not proof that a locally
produced report is authentic and not an automated certification decision.

Participants control the Phase 1 container and filesystem, so deliberate report
tampering cannot be excluded. This is an accepted Phase 1 risk. Tamper-resistant
provenance is a Phase 2 portal responsibility and applies to runs managed within
OBL-controlled infrastructure; uploading a local report does not by itself
establish authenticity.
