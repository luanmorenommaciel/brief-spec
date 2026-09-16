# Copilot cloud bridge

`brief-spec setup copilot --scope project --project <path>` installs a network-free bridge into
the target repository:

- `.github/hooks/brief-spec.json`
- `.github/brief-spec/brief-spec.pyz`
- `.github/instructions/brief-spec.instructions.md`
- shared skills under `.agents/skills/`

The zipapp is built from the same Python core used locally. Copilot cloud jobs do not inherit
personal plugins and run in an ephemeral, network-restricted Linux sandbox, so the bridge keeps the
hook executable inside the repository. The bridge stores only ephemeral session counters during a
job and does not send data to an external service.

The repository hook uses the PascalCase event form understood by VS Code and accepted by Copilot
CLI/cloud. Brief-Spec emits the native Copilot response fields together with the VS Code-compatible
hook envelope where the response shapes differ. This keeps one checked-in bridge usable across
local editor sessions and ephemeral cloud jobs.

Command hooks cannot add context from the prompt event in Copilot, so the bridge delivers the task
classification once, on the first tool result of each classified task.
