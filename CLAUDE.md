# CLAUDE.md - media-apply

## GitHub Workflow

- GitHub repository `Levtos/benni_media_apply` is the active code source; `Levtos/control` is the active workflow and documentation source.
- Relevant work requires a GitHub Issue in `Levtos/control`.
- Before work starts, read the issue description and all issue notes.
- Document current state, decisions, scope changes, tests, commits, pull requests, blockers, and completion in the issue or PR.
- Code changes happen in the matching GitHub repository; the publication remote must point to GitHub.
- Use feature branches and pull requests; do not push directly to `main`.
- Release flow: merge version bumps to GitHub `main`, create the GitHub tag, and let GitHub Actions create or verify the HACS release.
- GitLab, Plane, and Forgejo are retired and must not be used for active work.
- Full rules live in `Levtos/control/AGENTS.md`, `Levtos/control/CLAUDE.md`, and `Levtos/control/docs/workflow/`.

## Project-Memory Bootstrap

- Before significant work, read the matching GitHub issue description and all notes, then `Levtos/control/docs/workflow/README.md`, its linked workflow documents, and relevant `Levtos/control` wiki pages.
- GitHub is the workflow truth and the distribution source. GitLab, Plane, and Forgejo are retired and must not be used.
- Stay inside the decided issue scope: no side quests and no overwriting foreign branches or dirty worktrees.
- Use the smallest sufficient verification for the risk tier. Stable changes to behavior, contracts, operations, or rules belong in the wiki; use live evidence when runtime behavior must be proved. Completion notes must document wiki impact, verification/tests, release state where applicable, and required live evidence.

## Safety

- Do not put secrets in issues, commits, logs, or reports.
- Do not touch production Home Assistant systems without explicit approval.
- No admin, delete, runner, or bulk actions without explicit approval.

## UX-Frontend-Standard (verbindlich)

Für jede UX-/Frontend-Arbeit gilt der verbindliche, fleet-weite UX-, Technologie- und
Designstandard. Kanonische Quelle: ADR `ha-platform/control:docs/adr/0001-ux-frontend-standard.md`
(Issue `control#58`). Kurzform: Svelte 5 · Vite · TypeScript · Bits UI · shadcn-svelte ·
Tailwind · CSS Custom Properties · Lucide; Design "Graphite Dark – semantic accent system";
zentrale UX = statisches Bundle + dünnes UX-Gateway (primär HA-Ingress); versionierte/typisierte
Contracts. Details und Abweichungsprozess: `docs/ux-frontend-standard.md` und das ADR. Bestehende
Regeln werden dadurch ergänzt, nie überschrieben oder entfernt.
