# Repository bootstrap: benni_media_apply

GitHub is the active source of truth for this repository's code, Issues, pull requests, Actions, releases, and HACS distribution. The central operating model and Project Memory are in https://github.com/Levtos/control.

## Before work

- Read the complete GitHub Issue and all comments.
- Add the Issue to the Platform Workflow Project and set known Status, Type, Priority, Owner, Scope, Evidence, and Module fields.
- Read relevant docs in Levtos/control and this repository's functional specification.
- Use a fresh clone or isolated worktree from the verified default branch. Never overwrite a dirty checkout.

## Work and evidence

- Benni decides product behavior. Do not invent behavior or expand scope.
- Use one active implementation agent per Issue.
- Work on a branch, run focused tests, open a PR, inspect checks, and merge server-side.
- Record the commit, push actor, PR actor, merge actor, checks, merge SHA, release, and HACS evidence on the Issue.
- New unrelated findings become separate Issues.
- Do not print tokens, credentials, private configuration, or personal data.

## Releases

- Stable vX.Y.Z releases are standard.
- Alpha, beta, RC, and other pre-releases require Benni's explicit decision.
- The manifest version must match the stable tag without v.
- The release Action creates a normal, non-draft, non-prerelease GitHub Release.
- The technical chain ends at a visible HACS update.
- Keep Testing and Tests Pass separate from Benni's Live and Live Verified gate.

## Boundaries

Repository-local tests and central release automation are separate layers. Do not add runners or unrelated CI gates. Do not force-push, delete/replace tags, or change Home Assistant, LXC 104, MCPHub, or LeanCTX from this repository. Use git, gh, and the helper in Levtos/control; avoid interactive credential dialogs.
