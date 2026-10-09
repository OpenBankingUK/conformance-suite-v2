# Usage guide

[Project README](../README.md) | [Installation guide](INSTALLATION_GUIDE.md) |
[Developer guide](DEVELOPER_GUIDE.md)

This guide covers building, executing and understanding a test plan through the
browser, followed by advanced CLI and local REST reference.

## Contents

- [Before you run](#before-you-run)
- [Browser builder workflow](#browser-builder-workflow)
- [Monitor a run and interpret results](#monitor-a-run-and-interpret-results)
- [Give beta feedback](#give-beta-feedback)
- [CLI plan execution](#advanced-reference-cli-plan-execution)
- [PSU authorisation and pipelines](#psu-authorisation-and-pipeline-runs)
- [REST launch and monitoring](#browser-and-rest-launch)
- [Supported catalogues](#bundled-catalogues)
- [Outputs and exit codes](#outputs-and-exit-codes)
- [Troubleshooting](#troubleshooting)

## Beta scope

**Beta only — not for certification.** FCS v2 `2.0.0-beta.10` is an MVP for
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

## Before you run

Start the local UI using the [Installation guide](INSTALLATION_GUIDE.md), then
open `https://127.0.0.1:8443/` for Docker or `make dev`. No checked-in participant
config or manually written JSON plan is needed for the browser workflow.

Have your ASPSP sandbox's OpenID discovery URL, client registration details,
required transport/signing credentials, and business test data ready. Which
fields are required depends on the specification and selected endpoints; the
builder identifies them. For PSU authorisation, use the exact redirect URI
registered for your client, not necessarily the UI address. DCR also requires a
software statement assertion and registration audience; see the
[DCR contract](DCR_3_4_PARITY_CONTRACT.md#participant-and-operator-workflow).

Use test credentials and data appropriate for your environment. Runs send real
requests to the configured ASPSP and can create resources; stopping the tool
does not undo requests already accepted by the ASPSP. Do not share private keys,
tokens or exports containing secrets.

Pasted/uploaded credentials are held in the server-side builder session.
Use **Keep**, **Replace** or **Clear** to manage stored credentials; Clear removes
that credential from the draft. Safe exports omit secret values, not all
potentially sensitive business data. Review every artifact before sharing.
For repeated or unattended runs, the optional read-only
[`/certs` mount](DOCKER_GUIDE.md#advanced-optional-certificate-mount) keeps file
contents out of the browser draft. Paths must exist inside the running
host/container, not just on your computer.

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

For **DCR 3.4**, discovery and security are separate pages: **Preview discovery**
shows metadata without filling the Read/Write fields described above. Supply the
DCR credentials and request values, then go to review; DCR has no business-data
page. Its token requests are generated setup steps, not scope selections.

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
preview. **Those labels do not make beta.10 runs valid for certification.**
Generated tests are read-only: participants cannot select exact generated
tests. Lower-level request and assertion details stay collapsed under audit
details.

## Monitor a run and interpret results

After **Launch run**, the run detail page shows status, progress, execution logs
and PSU actions. With JavaScript enabled, active panels update automatically;
without it, the page refreshes. Complete any PSU authorisation action in the
browser and return to the run page. A run waiting for consent has not finished.

Inspect the completed report, not only the overall status. Failed checks include
step messages and HTTP evidence; skipped checks can mean an endpoint was not
selected or a prerequisite failed, not that the check passed. DCR reports
include ordered scenario, case and step outcomes. The report also records the
selected scope, generated tests and non-certifying reasons.

Download the **JSON** masked report and execution log from the run detail page
before stopping a disposable container. Logs help diagnose failures; the
structured result is the authoritative machine-readable output. Masking removes
recognised credentials and tokens, but identifiers, URLs, paths and arbitrary
business/narrative data can remain sensitive.
Browser log downloads are JSON arrays of events; CLI log files on disk use
NDJSON (one JSON event per line).

Browser sessions, stored evidence and active execution are different lifecycles.
A [persistent Docker volume](DOCKER_GUIDE.md#advanced-persistent-local-run)
retains session files, results and logs, but does not resume an interrupted run
or preserve the process-local active run/report stores. Save downloads you need
and launch a new run after restarting. Do not infer success from an old result.

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

### PSU authorisation and pipeline runs

An optional top-level `execution` block in the plan sets who completes PSU
authorisation: the PSU in a browser (`manual`, the default) or the sandbox
(`auto-approve`).

```json
"execution": {
  "psuAuthorization": {
    "mode": "auto-approve",
    "headers": { "x-sandbox-auto-approve": "true" },
    "parameters": { "headless": true }
  }
}
```

| Mode | Behaviour |
| --- | --- |
| `manual` (default) | The CLI prints each PSU authorisation URL to stderr and starts a built-in HTTPS listener for the plan's `redirectUri`. Complete the consent in a browser and the run continues. Use `--open-browser` to open the URL automatically. |
| `auto-approve` | The sandbox completes authorisation. The runner sends the authorisation request with an HTTP client, not a browser, so the sandbox must auto-approve and redirect straight back to `redirectUri` with a code. Login and consent pages are not automated. This mode suits unattended CI/CD pipelines. |

Auto-approval is a sandbox test facility, not an Open Banking UK or FAPI
concept, and ASPSPs usually have to enable it for your client. Ozone and the
legacy FCS call it "headless". A plan that still uses `"mode": "headless"` is
rejected with a message asking you to change it to `auto-approve`. The
`"headless": true` parameter above is just an example of a sandbox-specific
custom parameter.

- `headers` are only valid in `auto-approve` mode. They are sent on the
  authorisation request only and never on resource calls. You cannot override
  HTTP framing and hop-by-hop headers such as `Host`, `Content-Length` or
  `Connection`.
- `parameters` are valid in both modes. They are added as claims in the signed
  request object (FAPI 1 Advanced Part 2 §5.2.2). They are not sent as
  unsigned query parameters, except for pre-signed request objects that the
  runner cannot amend. Values may be strings, numbers or booleans. You cannot
  override OAuth/OIDC names that the runner generates, such as `state`,
  `nonce`, `scope` or `request`.
- Results record the mode and the header and parameter names under
  `execution.psuAuthorization`, but never their values. Header values that
  look sensitive are also blanked in the stored plan snapshot. Auto-approve
  mode does not change certification eligibility.
- If the ASPSP redirects an auto-approve request to one of its own pages (for
  example an error page) instead of `redirectUri`, the runner follows up to
  three redirects on the same origin. It sends only the cookies the ASPSP set
  and never resends your custom headers. The page's visible text (scripts,
  markup and hidden fields removed, at most 2 KB) is recorded as `errorPage`
  evidence, and the start of it is added to the step message. Redirects to
  another origin are reported but not followed.

The manual-mode listener binds to the host and port of `redirectUri`. Inside
the container it binds to all interfaces. It uses the container certificate
when one is available; otherwise it uses a temporary self-signed certificate.
If you can't bind that port (for example, port 443 without root), pass
`--callback-listen HOST:PORT` and forward the registered redirect to it.

At the end of each run, the CLI prints a short summary to stdout. It shows the
overall result, step counts, the PSU authorisation mode, any reasons the run
is not certification-eligible, the failed steps with HTTP status and message,
and the paths of the result file and execution log. For pipeline gating, use
the exit code or `python -m conformance.result_gate <result-file>`.

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
[Read/Write endpoint requirement matrix](READ_WRITE_ENDPOINT_REQUIREMENTS.md)
records Mandatory, Conditional and Optional classifications from the individual
resource pages for v4.0.1, v4.0.0 and v3.1.11, including dependencies and source
discrepancies.

DCR plans instead use family `OBL_DCR`, specification
`dynamic-client-registration`, version `3.4`, top-level `endpoints`, and
`dynamicClientRegistration`; they must not contain `resourceGroups` or
`businessTestData`. See
[`DCR_3_4_PARITY_CONTRACT.md`](DCR_3_4_PARITY_CONTRACT.md) for the
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

Run detail, result downloads, and structured execution logs keep the existing
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

### REST lifecycle

For a local server started with `make serve` (plain HTTP), submit a
complete, validated plan file:

```bash
curl --fail-with-body -H 'Content-Type: application/json' \
  --data-binary @/absolute/path/test-plan.json \
  http://127.0.0.1:8443/api/runs/
```

Creation returns HTTP `201` with the run ID and status; execution is
asynchronous. Invalid input returns `400`, and an already active run returns
`409`. Substitute the returned ID in these routes:

| Method and route | Purpose |
| --- | --- |
| `GET /api/runs/<run_id>/` | Poll status and progress. |
| `GET /api/runs/<run_id>/result/` | Retrieve the completed structured JSON result. |
| `GET /api/runs/<run_id>/log/` | Retrieve a JSON array snapshot of execution-log events, including while running. |

Result requests before completion return `409`. Poll until terminal status,
then retrieve evidence; an execution error can mean no result was produced.
The log endpoint can be re-polled during execution; its JSON array format is
different from the CLI's on-disk NDJSON log.
Use the structured result gate as well as checking HTTP responses.

For Docker or `make dev`, use HTTPS instead and configure your HTTP client's
trust for the local certificate. Do not disable verification for ASPSP traffic.
The REST API is unauthenticated and rejects non-loopback callers by default.
Keep it local; do not disable that guard simply to work around a `403`.
Docker networking may present a bridge address rather than loopback to the
application; use the local source server for this programmatic workflow rather
than assuming every loopback host publish passes the application guard.

The JSON snippets above illustrate structure, not runnable sandbox
configuration. Supply all fields and credentials required by your selected
scope. DCR uses the shared canonical credential contract: supply each credential
once, by absolute file reference or inline material. REST requests containing
inline secrets are sensitive, just like secret-bearing plan files.
Other catalogue `file_reference` runtime inputs are rejected at the REST
boundary. See the [DCR contract](DCR_3_4_PARITY_CONTRACT.md).

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
lives in the [legacy benchmark mapping](FCS_LEGACY_BENCHMARK_MAPPING.md).

Dynamic Client Registration 3.4 is first-class across browser, CLI, and local
REST execution. It has no resource-group page or resource-server/business-data
configuration.

## Outputs and exit codes

The runner writes a structured result JSON to `resultOutputPath`, defaulting to
`out/test-results.json`, and writes an NDJSON execution log to
`executionLogPath`, defaulting to `out/execution-log.ndjson`.
With `CONFORMANCE_DATA_DIR` set (the container defaults it to `/data`), the
defaults instead land under that root's `results/` and `logs/` directories.
The CLI summary prints the actual output paths.

CLI exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | All selected checks passed. |
| `1` | Execution completed with failed checks. |
| `2` | Config, canonical test plan, or catalogue compilation input was invalid. |
| `3` | Result or execution-log output could not be written. |

Set `CONFORMANCE_DEVELOPER_MODE=true` only for local debugging. It disables
masking in developer-visible logs and must never be enabled in release builds.

### CLI progress and cancellation

Preparation, listener/client setup, step progress and PSU prompts are written
to stderr; the final summary is written to stdout. A manual PSU URL means the
run is waiting for browser authorisation. Use masked results and execution logs
for payload-level diagnosis. `NO_COLOR` (including an empty value) disables
terminal colour; redirected output and structured files remain plain text.

Press **Ctrl+C** to cancel (exit `130`), or send **SIGTERM** to the Python PID
printed by the CLI (exit `143`), not the parent `uv` process. Cancellation closes
the client and callback listener but does not undo ASPSP requests. Native
blocking operations and in-flight HTTP timeouts can delay cleanup.

Interrupted execution does not save partial results or flush buffered execution
logs. Existing evidence is not deleted. If cancellation occurs during final
publication, already atomically published files may remain; check the process
exit code rather than treating an old file as evidence for this run. SIGKILL
does not permit cleanup. These cancellation rules apply to CLI execution, not
to browser/API run cancellation.

## Troubleshooting

| Symptom | Next action |
| --- | --- |
| UI will not open | Check the [installation troubleshooting](INSTALLATION_GUIDE.md#troubleshooting), including HTTPS versus HTTP and port conflicts. |
| Review blocks launch | Follow each **Fix** link, supply fields marked **Required to run**, and correct plan/import warnings. Import recovery does not relax launch validation. |
| Imported safe export cannot run | Re-enter omitted credentials and runtime secrets. Read/Write CLI/REST plans must include `openApiDocumentUpdate`; the browser warns and selects the latest when it is absent. |
| Certificate/file not found | Use an absolute path inside the executing host/container, or paste/upload in the browser. Do not supply both path and inline PEM for one credential. |
| Run waits for PSU | Complete the displayed authorisation action; check the exact registered redirect URI, callback listener port and local certificate trust. |
| `auto-approve` reaches a login page | Ask your sandbox operator to enable automatic consent for your test client; the runner does not automate login/consent pages. |
| A case is skipped after a failure | Inspect the failed prerequisite and dependent steps in the structured report; skipped is not passed. |
| REST returns `403` | Confirm the request reaches the server from loopback; do not expose or remove the guard as a shortcut. |
| Feedback download expired | Prepare a new report and download promptly; bundles are process-local and short-lived. |

For startup before the first CLI progress message, dependency setup may be the
delay. Developer-only probes are documented in the
[Developer guide](DEVELOPER_GUIDE.md#shared-plan-document-contract).

## Internal certification boundary

This beta is not approved for certification, regardless of report labels or
validator output. The internal validator workflow and the limits of local
report assurance are documented for maintainers in the
[Developer guide](DEVELOPER_GUIDE.md#certification-validation) and
[decision log](DECISION_LOG.md#dl-003-local-report-assurance).
