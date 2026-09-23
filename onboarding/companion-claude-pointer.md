## Governed Agentic Companion front door

This workspace contains the **Governed Agentic Companion** governed front door in the
`__COMPANION_REPO__/` subfolder. For any request the companion's specialists cover
(platform/infrastructure, security, and any specialist you have added), route it through that
front door rather than answering ad hoc.

- Packaged agents: install the Claude Code plugin from `__COMPANION_REPO__/plugin/`, then invoke
  `companion-orchestrator` (routes automatically) or a specialist (`companion-platform`,
  `companion-security`, plus your own).
- Deterministic engine: run `cd __COMPANION_REPO__ && ./run.sh <command>` (e.g. `status`,
  `ask "<question>"`, `gate`).
- Governance is always on (`__COMPANION_REPO__/PRINCIPLES.md` + the GovernanceGate): produce
  review-ready artifacts only, a human deploys; systems of record are read-only; writes go only
  to Jira/Confluence/GitHub.

This block is a routing pointer only; it does not override your project's own instructions.
