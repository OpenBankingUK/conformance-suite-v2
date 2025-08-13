# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.9.4] - 2025-08-13

### Added

- Created this new `changelog` file for tracking changes to the project going forward in one place.

### Changed

- Update version to v1.9.4 final release

## [1.9.4-beta3] - 2025-08-12

### Changed

- Updated `DateTimes` used in `OB-400-TRA-105110` and `OB-400-TRA-105120` to 2025.

## [1.9.4-beta2] - 2025-07-29

### Removed

- `x-fapi-financial-id` header test (`OB-313-ACC-000100`) from v4 AIS manifest tests (#227).

## [1.9.4-beta1] - 2025-07-25

### Fixed

- [read-write-api-specs/issues/188](https://github.com/OpenBankingUK/read-write-api-specs/issues/188) - Fixes typo in AIS `OB_CodeMnemonic` regex pattern

## [1.9.3] - 2025-05-30

For changelogs for v1.9.3 and earlier, please see the changelog files in the [`docs/releases/` directory](docs/releases).

[Unreleased]: https://github.com/OpenBankingUK/conformance-suite/compare/v1.9.4...HEAD
[1.9.4]: https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.9.4-beta3...v1.9.4
[1.9.4-beta3]: https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.9.4-beta2...v1.9.4-beta3
[1.9.4-beta2]: https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.9.4-beta1...v1.9.4-beta2
[1.9.4-beta1]: https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.9.3...v1.9.4-beta1
[1.9.3]: https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.9.3

---

## Guidance for Changelog

As seen on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/#how).

### Guiding Principles
* Changelogs are for humans, not machines.
* There should be an entry for every single version.
* The same types of changes should be grouped.
* Versions and sections should be linkable.
* The latest version comes first.
* The release date of each version is displayed.

### Types of changes
* `Added` for new features.
* `Changed` for changes in existing functionality.
* `Deprecated` for soon-to-be removed features.
* `Removed` for now removed features.
* `Fixed` for any bug fixes.
* `Security` in case of vulnerabilities.