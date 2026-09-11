# Suite detail drift badge copy (#972)

Status: implemented
Class: bug-fix

## Decision

Rename the detail-page badge from「磁盘导出物漂移」to「库内容漂移」— the UI compares
`exported_content_sha256` vs `content_sha256` (library fingerprints), not on-disk
files. Variable renamed to `libraryContentDrift` for clarity.

## Alternatives

- Backend disk probe for real filesystem drift — out of scope; issue allows copy fix.

## Verification

- Manual: badge text on suite detail when export fingerprint ≠ current content sha.

## Revisit

If disk-level integrity is needed, add a separate backend signal and badge.
