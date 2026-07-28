# Claude bootstrap: benni_media_apply

- GitHub is the active source of truth; Levtos/control contains the central rules and Project Memory.
- Read the full Issue and comments, add it to Platform Workflow, and set known fields before coding.
- Benni decides behavior. Keep implementation narrow and create a new Issue for unrelated findings.
- Use a clean clone/worktree, branch, PR, checks, and server-side merge. Do not overwrite dirty worktrees.
- Stable releases are standard; pre-releases need Benni's explicit decision.
- End technical E2E at visible HACS availability. Testing is not Live; Benni owns Live / Live Verified.
- Use git, gh, and control/tools/github_workflow.py. Never output secrets.
- Keep private HA configuration and LXC 104/MCPHub/LeanCTX outside this repository.
