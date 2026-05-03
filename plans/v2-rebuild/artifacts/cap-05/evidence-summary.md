# Capability 5 C2 Evidence Summary

AQ2-74 / Capability 5 closes the Job submission loop with `submit_job`,
`review_complete`, inline Decision/Learning creation, and the cap-5 race and
atomicity coverage.

## Final Verification

- Full Docker pytest matrix: `final-test-matrix.txt`
- mypy strict: `final-mypy-strict.txt`
- ruff: `final-ruff.txt`
- DB schema shape: `final-db-shape.txt`
- Cap-5 capabilities prose greps: `capabilities-md-greps.txt`
- Cap-5 lock greps: `cap05-locks-grep.txt`
- Redaction and secret scan: `redaction-pass.txt`
- AQ2-73 out-of-scope comment: `aq2-73-status.txt`

## Locked Query Evidence

- Decisions attached lookup: `explain-decisions-attached-lookup.txt`
- Learnings attached lookup: `explain-learnings-attached-lookup.txt`

## Story Evidence

- Story 5.1 schema/models: `schema-decisions.txt`, `schema-learnings.txt`,
  `alembic-roundtrip.txt`, `discriminator-selection.xml`
- Story 5.2 submit done path: `submit-done-success.xml`,
  `contract-violation-cases.xml`, `mcp-submit-multipart.xml`,
  `parity-submit-done.xml`
- Story 5.3 remaining outcomes: `submit-pending-review.xml`,
  `submit-failed.xml`, `submit-blocked.xml`,
  `submit-blocked-denials.xml`, `parity-submit-other-outcomes.xml`
- Story 5.4 review complete and state matrix:
  `review-complete-done.xml`, `review-complete-failed.xml`,
  `state-machine-matrix.xml`, `parity-review-complete.xml`
- Story 5.5 inline D&L atomicity and EXPLAIN:
  `submit-dl-atomicity-1.xml`, `submit-dl-atomicity-2.xml`,
  `submit-dl-atomicity-3.xml`, `explain-decisions-attached-lookup.txt`,
  `explain-learnings-attached-lookup.txt`
- Story 5.6 MCP richness and races: `mcp-instructions-cap05.xml`,
  `mcp-annotations-cap05.xml`, `mcp-submit-multipart.xml`,
  `race-50-concurrent-submit.xml`, `sweep-vs-submit-sweep-wins.xml`,
  `sweep-vs-submit-submit-wins.xml`, `fastmcp-version-pin-check.txt`

## C2 Status

C2 is ready for Ghost review after the Story 5.7 commit is pushed and CI is
green. No PR is opened until Ghost approves the C2 evidence.
