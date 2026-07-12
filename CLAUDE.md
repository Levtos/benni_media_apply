# CLAUDE.md - media-apply

## GitLab Workflow

- GitLab project `ha-platform/control` is the central workflow truth.
- Relevant work requires a GitLab issue in `ha-platform/control`.
- Before work starts, read the issue description and all issue notes.
- Document current state, decisions, scope changes, tests, commits, merge requests, blockers, and completion in the issue.
- Code changes happen in the matching GitLab repository. `origin` must point to GitLab.
- GitHub is only the public distribution and HACS mirror. Do not develop directly on GitHub and do not push manually to GitHub.
- Release flow: push branches and tags to GitLab `origin`; GitLab mirrors them to GitHub; create GitHub Release objects only for already mirrored tags; HACS consumes those GitHub Releases.
- Plane and Forgejo are historical sources only and are not used for active work.
- Full rules live in `ha-platform/control/AGENTS.md`, `ha-platform/control/CLAUDE.md`, and `ha-platform/control/docs/workflow/`.

## Safety

- Do not put secrets in issues, commits, logs, or reports.
- Do not touch production Home Assistant systems without explicit approval.
- No admin, delete, runner, or bulk actions without explicit approval.
