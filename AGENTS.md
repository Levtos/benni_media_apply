# AGENTS.md - media-apply

## GitHub Workflow

- GitHub repository `Levtos/benni_media_apply` is the active code source; `Levtos/control` is the active workflow and documentation source.
- Relevant work requires a GitHub Issue in `Levtos/control`.
- Before work starts, read the issue description and all issue notes.
- Work under exactly one Issue agent, recorded as `agent:codex` or `agent:claude`; technical completion is not Live.
- Document current state, decisions, scope changes, tests, commits, pull requests, blockers, and completion in the issue or PR.
- Code changes happen in the matching GitHub repository; the publication remote must point to GitHub.
- Use feature branches and pull requests; do not push directly to `main`.
- GitLab, Plane, and Forgejo are retired and must not be used for active work.
- Full rules live in `Levtos/control/AGENTS.md`, `Levtos/control/CLAUDE.md`, and `Levtos/control/docs/workflow/`.

## Project-Memory Bootstrap

- Before significant work, read the matching GitHub issue description and all notes, then `Levtos/control/docs/workflow/README.md`, its linked workflow documents, and relevant `Levtos/control/docs/` pages.
- GitHub is the workflow truth and the distribution source. GitLab, Plane, and Forgejo are retired and must not be used.
- Stay inside the decided issue scope: no side quests and no overwriting foreign branches or dirty worktrees.
- Use the smallest sufficient verification for the risk tier. Stable changes to behavior, contracts, operations, or rules belong in the versioned control/docs/ Project Memory; use live evidence when runtime behavior must be proved. Completion notes must document documentation impact, verification/tests, release state where applicable, and required live evidence.

## Safety

- Do not put secrets in issues, commits, logs, or reports.
- Do not touch production Home Assistant systems without explicit approval.
- No admin, delete, runner, or bulk actions without explicit approval.

## UX-Frontend-Standard

For UX/frontend work, follow the current canonical
ADR `Levtos/control/docs/adr/0001-ux-frontend-standard.md` and
`Levtos/control#17`. Detailed rules remain in the central control
documentation and are not duplicated in this repository bridge.

## Local Completion Contract

- For this repository, complete the technical chain with focused repository
  tests or proportionate technical checks → pull request → server-side merge
  → stable release/tag through the existing repository workflow when this
  repository publishes a release.
- Live installation, reload, restart, and Live/Live-Verified verification
  remain a separate user/Benni gate.
