# CI security checks and automatic publication

This guide replaces the former DHI risk-approval runbook. Repository code does
not configure GitHub rulesets or environment protection rules; administrators
must coordinate the settings below with the workflow rollout.

## Retained checks

| Check | Source |
|---|---|
| `Check` | CI: full tracked-file secret scan, Ruff including security rules, mypy, offline tests and coverage |
| `Docker Build` | CI: build and smoke test |
| `Image (linux/amd64)` | CI: exact amd64 image, evidence and smoke test |
| `Image (linux/arm64)` | CI: exact arm64 image, evidence and smoke test |
| `code/snyk (Standards)` | Existing external Snyk PR integration |
| CodeQL | GitHub default setup; required code-scanning results at High or higher |

Snyk reports its own PR status. No Actions or promotion workflow invokes Snyk or
requires `SNYK_TOKEN`. GitHub secret scanning/push protection, Dependabot updates,
normal PR reviews, Code Owners, and fork workflow approval policy remain.

Custom blocking Scout, Snyk Container and pip-audit scans, DHI provenance/VEX risk
assessment, exception receipts, promotion rescans, and daily vulnerability-issue
sync are removed. PR source/dependency checks are **not equivalent** to the
removed multi-platform OS/container scans or surveillance of published images.
Existing vulnerability issues and historical reports are not marked fixed or
deleted; automatic updates stop.

For PRs into `main`, open **Docker Scout preview** from the PR checks and
expand **View vulnerability report (packages and CVEs)** for the final
`linux/amd64` and `linux/arm64` images' severity counts and package findings.
The check applies Docker's signature-verified VEX for the exact pinned runtime
base on each platform, so findings assessed as not affecting that base are
filtered just as in a local scan using the same VEX file. An unavailable scan
or VEX retrieval is labelled separately from a successful scan with no findings.
`Image (linux/amd64)` still shows the **Docker Scout impact** comparison with
the published `2.0.0-beta-latest` and whether the runtime base digest changed.
Neither Scout report is a merge or publication requirement. See
[CI/CD Strategy §3.2](CICD_STRATEGY.md#docker-scout-results-on-published-images)
for why published Scout results often stay the same between betas.

## Docker Scout VEX statements

Docker signs a VEX document for each DHI image that marks many base-package
CVEs as not affecting it. What Docker Hub's Scout view applies can differ by
image and release. The following comparison was measured on 2026-11-17 for
`2.0.0-beta.21` (`linux/amd64`, runtime base
`dhi.io/python:3.14-debian13@sha256:d20d953b…`):

| Scout view | Vulnerabilities |
|---|---|
| Published image, as shown on Docker Hub | 25 in 15 packages: 0 critical, 2 high, 6 medium, 12 low, 5 unspecified |
| Same image with the DHI base VEX (`--vex-location`) | 7 in 7 packages: 0 critical, 0 high, 2 medium, 0 low, 5 unspecified |

For that image, the base VEX had 191 statements. Scout attached only 47 to our
image, all justified as `vulnerable_code_cannot_be_controlled_by_adversary`.
It omitted the `inline_mitigations_already_exist` statements, including those
covering the `libexpat1` High findings. This behavior is not universal: for
`2.0.0-beta.27` on `linux/amd64`, the registry scan did apply the DHI VEX
statement marking musl CVE-2026-107891 as `not_affected`. The PR preview now
fetches and verifies the signed VEX for its exact runtime base and supplies it
to the archive scan; if it cannot verify that document, the platform result is
reported as unavailable rather than showing unfiltered counts.

Scout also ignores a VEX document embedded in the image filesystem when the
image has attestations, as ours do, so a Dockerfile change cannot fix this.

The pipeline does **not** attach VEX or create Scout exceptions. Either is a
public security claim about our image, and Docker documents that an attached
VEX document cannot be removed. Do not attach one without security owner
approval. To evaluate it, use a published preview image:

1. Fetch Docker's signed VEX for the exact runtime base. It is
   platform-specific, so repeat for `linux/arm64`. This needs a `dhi.io` login:

   ```bash
   docker scout vex get --verify --platform linux/amd64 \
     --output base-amd64.vex.json \
     registry://dhi.io/python:3.14-alpine@sha256:<runtime-base-digest>
   ```

2. Confirm the effect locally, without changing the registry:

   ```bash
   docker scout cves --platform linux/amd64 --vex-location base-amd64.vex.json \
     registry://openbanking/conformance-suite-v2:<preview-version>
   ```

3. Only after approval, attach it to that preview image with credentials that
   can write to the repository, then check Docker Hub's view of the image:

   ```bash
   docker scout attestation add --referrer \
     --predicate-type https://openvex.dev/ns/v0.2.0 \
     --file base-amd64.vex.json \
     openbanking/conformance-suite-v2:<preview-version>
   ```

Scout exceptions created in the Docker Scout Dashboard are an alternative that
needs the same approval. Ask Docker support whether derived images should
inherit the full DHI VEX before choosing either route.

## Required PR checks

Update only the affected check requirements, preserving review rules, bypass
actors, strict checks, and other branch protections:

- `main & develop` (`15781582`) and `release branch checks` (`24119519`):
  remove `Vulnerability Scan`; keep the five CI/Snyk statuses above and the
  existing CodeQL rule.
- Preview ruleset (`27127788`, named `preview vulnerability check`):
  replace its sole `Vulnerability Scan` requirement with `code/snyk (Standards)`.
  Confirm Snyk reports for PRs targeting preview branches before relying on it.

Do not leave a required check whose producer has been deleted, invent an
always-successful replacement, or drop Snyk when it is missing. Repair the
integration's branch coverage instead.

## Publishing environments

Keep `preview-release`, `beta-release`, and `ga-release` as credential boundaries:

1. Restrict deployment branches to **only `main`**, with no tag patterns.
2. Remove required reviewers and wait timers. There is no security or
   publication approval after a PR is merged and eligible push CI passes.
3. Preserve each environment's write-capable `DOCKER_ORG_ACCESS_TOKEN`.
   Never move this token into repository or Dependabot secrets.
4. Keep the read-only `DOCKER_ORG_USERNAME` and `DOCKER_ORG_ACCESS_TOKEN`
   credentials available for DHI pulls in repository and Dependabot scopes.

The reusable publisher retains `environment:` and checks out trusted `main`;
GitHub resolves its environment secret in preference to the read-only secret
passed by the caller. It validates the exact CI run, candidate metadata,
archive checksums, source branch/merged PR, and publication digests. It never
rebuilds or executes candidate code with publishing credentials.

The unused `security-exception` environment and stored `SNYK_TOKEN` can remain
inert during migration. They are not used by the new workflows.

## Cutover and verification

1. Save current non-secret ruleset/environment configuration and inspect pending
   runs. Do not release an old queued deployment by removing its reviewer gate.
2. Coordinate removal of the retired required check and retirement of the old
   assessment/rescan automation with the reviewed workflow change on `main`.
   Cancel only confirmed superseded scanner runs.
3. Apply the publication environment restrictions before removing reviewers.
   Re-read settings to verify credentials and unrelated protections were retained.
4. Main is the active development and beta-publication branch. Only optional
   preview branches need to incorporate main's pipeline changes. `workflow_run`
   executes from the default branch, and promotion still requires matching
   pipeline files, including for older main candidates.
5. Verify a fresh PR receives Snyk, CodeQL where configured, and its normal CI
   checks without `Vulnerability Scan`. Verify the next eligible push CI starts
   publication without an approval pause.

Manual `promote-preview` and `promote-beta` dispatches remain recovery paths
from `main`; they no longer take an assessment run ID. The GA caller is disabled.
They still require a successful matching push CI run and immutable artifacts.
After successful publication, beta/GA release finalization remains unchanged.
Do not manufacture a version or publish an old candidate just to test rollout.

GA publication is paused while 2.0.0 is in beta: automatic promotion accepts
main beta candidates but explicitly skips stable/GA versions, and **Promote GA
image** stays disabled in Actions settings. A version bump alone cannot enable
GA. See [CI/CD Strategy §5.2](CICD_STRATEGY.md#52-ga-publication-paused).
