## What this changes

<!-- What it does and why. If it fixes a bug, name the fixture that
     reproduces it. -->

## Output impact

<!-- Did any recorded artifact change? If yes, paste the relevant part of the
     golden diff and explain every changed line. If no, say so — reviewers
     should not have to guess. -->

- [ ] No change to recorded output (`tools/golden/check.py` passes unchanged)
- [ ] Output changed deliberately, the diff is explained above, and the goldens
      were re-recorded in this PR
- [ ] This PR is a dependency/browser upgrade **and nothing else**, with its
      re-recording included

## Checklist

- [ ] No upstream types leak across a bridge boundary (docs/02 module table)
- [ ] Any new failure path has an exit code and a QA-report entry
- [ ] Goldens were not re-recorded to make a check pass
- [ ] No new runtime dependency without a docs/03 entry and a license check
- [ ] Security invariants (docs/04 §6) untouched or strengthened:
      credentials never enter a bundle, passwords stay redacted, auth profiles
      stay `0700`, the protected-viewer path still captures only rendered
      pages, sanitization still strips scripts/handlers/schemes, XML still goes
      through defusedxml, the 100 MB local-input guard still applies
- [ ] Does not weaken the capture ethics policy (docs/11-capture-ethics.md):
      no new way around access controls, DRM or CAPTCHAs, and no crawling
- [ ] A bug fix ships with a fixture that reproduces the bug
- [ ] `uv run python tools/verify.py` passes (or say here which steps you
      could not run, and why)
