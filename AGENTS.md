# Repository agent rules

## GitHub operations

- Use the connected GitHub application first for remote repository reads and
  writes, including commits, branch updates, pull requests, and verification.
- Use the terminal `git` client for local inspection, testing, diffs, and local
  commits. A terminal authentication failure does not mean the connected
  GitHub application is unavailable.
- Before offering patches, bundles, or manual file-copy instructions because a
  terminal push failed, verify whether the GitHub connector has write access
  and publish through it when authorized.
- Keep the normal installation path for the user: publish the tested change to
  the authorized branch, then instruct `git pull --ff-only origin <branch>`.
- Never force-push or update another branch without explicit authorization.
- This repository uses only `main` and `desarrollo`; do not create additional
  branches unless the user explicitly requests one.
