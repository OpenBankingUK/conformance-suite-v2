# Ubiquitous Language

## Conformance assets

| Term | Definition | Aliases to avoid |
|------|------------|------------------|
| **Specification** | The authoritative Open Banking standard source, including OpenAPI schemas and normative rules. | Spec file, OpenAPI when referring to the whole standard |
| **Certification Test Set** | An OBL-owned versioned set of tests used to define certification expectations independently of tool and API versions. | Participant test plan, custom run plan |
| **Suite Manifest** | The current implementation file for a **Certification Test Set**, defining executable conformance steps, assertions, metadata, and certification coverage for one suite. | Manifest, test manifest, suite file |
| **Suite** | A named catalog entry that binds a standard, API family, security profile, version, and **Suite Manifest**. | Test suite when referring to the catalog entry |
| **Participant Configuration** | Participant-owned environment and credential inputs needed to run against an ASPSP/model bank. | Config, discovery file, run config |
| **Discovery File** | A legacy previous-FCS JSON file containing environment/test inputs that should be replaced by structured **Participant Configuration** and **Run Plan** inputs. | Config, manifest |

## Run planning

| Term | Definition | Aliases to avoid |
|------|------------|------------------|
| **Run Plan** | A reusable participant-owned JSON representation of selected suite steps and run-specific test inputs, schema-versioned for parsing but not an official test-set version. | Test plan, config, manifest |
| **Plan Builder** | The UI workflow that creates or edits a **Run Plan** from a **Suite Manifest** and **Participant Configuration**. | Test case builder when discussing Rank 0 |
| **Selected Step** | A manifest step included in a **Run Plan** for execution. | Test selected, checked row |
| **Custom Test Value** | A run-specific value selected or entered by the participant instead of the suite default. | Override, custom config value |
| **Default Test Value** | A value supplied by the **Suite Manifest** profile when the **Run Plan** does not customise that key. | Standard value, config default |
| **Test Value Profile** | A named set of default test values declared by a **Suite Manifest**. | Profile, test data set |
| **Consumed Test Value Key** | A test-value key referenced by a specific step's request, assertion, or other placeholder-capable field. | Required key, override key |
| **Run Plan manifest hash** | A SHA-256 content hash of the suite manifest bytes, stored in the **Run Plan** to detect drift between the saved plan and the current manifest. | Manifest hash, plan hash |

## Certification

| Term | Definition | Aliases to avoid |
|------|------------|------------------|
| **Certification Eligibility** | The run-level judgement of whether a result can be submitted for certification. | Certifiable flag, certification status |
| **Exploratory Run** | A run that may execute valid tests but is not eligible for certification because it uses non-approved selections or custom inputs. | Non-certifiable run, custom run |
| **Step Outcome** | The pass, fail, warn, or skipped result for one executed step. | Test eligibility, certification result |
| **Certification Blocking Reason** | A machine-readable reason why **Certification Eligibility** is false. | Failure reason when referring to run eligibility |

## Relationships

- A **Specification** informs one or more **Certification Test Sets**.
- A **Certification Test Set** is currently implemented by one **Suite Manifest**.
- A **Suite** references exactly one **Suite Manifest**.
- A **Participant Configuration** supplies environment and credential inputs, not run-specific custom test choices.
- A **Run Plan** references one **Suite** and the corresponding **Certification Test Set** identity/hash, and contains zero or more **Selected Steps**.
- A **Run Plan** may contain **Custom Test Values**; omitted values fall back to **Default Test Values** from the selected **Test Value Profile**.
- A **Selected Step** may consume zero or more **Consumed Test Value Keys**.
- A **Step Outcome** can be failed while **Certification Eligibility** is also false for a separate **Certification Blocking Reason**.
- An **Exploratory Run** still produces normal **Step Outcomes**.

## Example dialogue

> **Dev:** "Is a participant's **Run Plan** a new version of the **Certification Test Set**?"
> **Domain expert:** "No, the **Certification Test Set** is OBL-owned and versioned; the **Run Plan** only references it and records participant choices."
> **Dev:** "Should the participant's model-bank URL and TLS credentials go in the **Run Plan**?"
> **Domain expert:** "No, those belong in **Participant Configuration**; the **Run Plan** should capture selected steps and **Custom Test Values**."
> **Dev:** "If a **Selected Step** fails after the participant changes a value, do we mark the step differently?"
> **Domain expert:** "The **Step Outcome** still records the normal failure, but the result should also show which **Consumed Test Value Keys** were customised."
> **Dev:** "And the whole run becomes non-certifiable?"
> **Domain expert:** "Yes, the run is an **Exploratory Run** with a **Certification Blocking Reason**, even though each step still has its own normal **Step Outcome**."

## Flagged ambiguities

- "config" was being used for both **Participant Configuration** and **Run Plan**; use **Participant Configuration** for environment/credential inputs and **Run Plan** for step selection plus custom test values.
- "manifest" was being used for both executable suite definitions and reusable participant plans; use **Suite Manifest** for tool-owned suite definitions and **Run Plan** for participant-owned choices.
- "discovery file" was being used as a broad synonym for previous-FCS inputs; reserve **Discovery File** for the legacy artifact being replaced.
- "test plan" was being used for both official certification tests and participant custom choices; use **Certification Test Set** for OBL-owned versioned tests and **Run Plan** for participant-owned selected steps and custom test inputs.
- "override" is implementation language; use **Custom Test Value** in product/UI copy and reserve `override` for JSON fields or code.
