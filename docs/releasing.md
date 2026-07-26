# Releasing platecli (maintainers)

## Preconditions

- **`main` is a protected branch.** Every change lands via pull request; direct pushes are
  rejected by the branch ruleset. Each `test` matrix leg and the `lint` job are required
  status checks, and merges are squash-only. There are 0 bypass actors, so no one can skip
  the gate — including for a release. If you add or rename a job in `ci.yml`, update the
  ruleset's required checks to match, or PRs will block forever waiting on a check that no
  longer reports.
- The commit you are about to tag is **green on CI** (`gh run list --branch main --limit 1`).
  Tags must only ever point at green commits: `release.yml` re-runs the full CI matrix
  via `uses: ./.github/workflows/ci.yml` before build/publish, so a red commit fails the
  release rather than shipping — but do not rely on that as your first line of defence.
- `pyproject.toml` `version` is the single source of truth (`bambu_cli.constants.VERSION`
  resolves from package metadata). `release.yml` fails if the tag does not match it.
- `CHANGELOG.md` entries moved from `Unreleased` into the new version heading.

## Release

1. Open a pull request containing the `version` bump in `pyproject.toml` and the
   `CHANGELOG.md` `Unreleased` → version move. Wait for the required checks, then
   squash-merge.
2. `git tag vX.Y.Z && git push --tags`
3. `release.yml` runs: CI matrix -> build (sdist+wheel, `twine check`, tag/version match)
   -> publish to PyPI via trusted publishing (`pypi` environment, `id-token: write`)
   -> GitHub Release with the dist artifacts attached.
4. Verify: `pip install --no-cache-dir platecli==X.Y.Z && plate --version`.
5. If the release touched FTPS, gcode confirm, slice validation, or job upload, run the
   [live-printer smoke](live-printer-smoke.md) with a printer attached.

## Rollback: there is no rollback, only yank + forward

PyPI uploads are **immutable**. A filename/version can never be reused, and trusted
publishing does not change that — deleting a release does not free the version number.
So a bad release is fixed by yanking it and shipping the next patch.

1. **Yank the bad version.** https://pypi.org/manage/project/platecli/releases/ ->
   the affected version -> *Options* -> *Yank*. Give a one-line reason (it is shown to
   users). Yanking leaves the files installable for anyone who pins `==X.Y.Z` (so pinned
   CI does not break) but removes it from ordinary resolution, so `pip install platecli`
   stops picking it up.
   - Only *delete* a release if it leaked a secret or shipped something legally
     unpublishable. Deletion breaks existing pins and still burns the version number.
2. **Never reuse the version number.** Do not re-tag `vX.Y.Z`, do not force-push the tag,
   do not attempt to re-upload. The next release is `X.Y.Z+1`.
   (Note: re-tagging and force-moving `vX.Y.Z` tags is also **mechanically enforced** by the
   `protect release tags` ruleset — git will reject those operations outright. If you hit
   that error while trying to fix something, that is expected behaviour, not a bug.)
3. **Fix forward.** Land the fix on `main` via a pull request (same PR-required flow as the
   normal release), confirm CI green, bump to `X.Y.Z+1`, tag, push.
4. **Changelog the yank.** Under the new version add:
   `### Fixed` / `- 0.2.3 was yanked from PyPI (<one-line reason>); use 0.2.4.`
   and leave the yanked version's own section in place with a `**Yanked.**` note. The
   changelog is the only durable record — PyPI's yank reason is easy to miss.
5. **Deal with the git tag.** Edit the GitHub Release for the yanked version to say
   *Yanked — see vX.Y.Z+1*. Prefer editing over deleting: the tag is the provenance link
   between the GitHub release and the PyPI artifacts, and rewriting history to "clean up"
   a release breaks that link permanently.

## If the PyPI publish itself is compromised

Rotate first, investigate second: remove the trusted publisher on
https://pypi.org/manage/project/platecli/settings/publishing/, yank every version you
cannot account for, then re-add the publisher only after the workflow is known good.
All actions in `release.yml` are SHA-pinned specifically so the `id-token: write` job
cannot be changed out from under us by an upstream tag move.

## Note on the old PyPI project

`bambu-local-cli` on PyPI is retained deliberately for anti-squatting; its 0.1.0 is
yanked. Do not delete that project and do not publish to it.
