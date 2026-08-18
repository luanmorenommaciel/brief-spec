# Operating hops

This repository: brief-spec

Everyday path (consult and this crew). Factory path is Converge only.

WorkHelm is the everyday plane (nuances, backlog, RPI). It calls the binaries below. It does not vendor them. The numbered hops are the engines, not a claim that WorkHelm runs last.

## Everyday

1. Talk to an LLM about intent.
2. Seamwise compiles a recipe into a TaskPlan (seams, swimlanes, legs).
   It does not write `tasks/`. It does not auto-approve topology.
3. A human accepts the topology.
4. `taskspec plan` / `taskspec batch` writes leaves under `tasks/`.
5. A human seals ready leaves with `taskspec gate --stamp` (HMAC v3).
   After a valid stamp the leaf stays `status: ready` and `signed_off: true`.
   That HMAC is the engine seal. `status: sealed` is forbidden.
   Hand-written or Tier-2 `signed_off` without a valid HMAC is not a seal.
6. TaskMesh runs already-authorized leaves only.
   It cannot widen scope, rewrite a signed leaf, or merge the user branch.
7. Brief-Spec explains closed (or phase) work. It is not the source of truth.
8. Keep-Spec holds the picture and continuous docs (ingest / observe / surfaces).
   It does not dispatch. Do not import brief-spec to render.

Task-Spec and TaskMesh are not alternatives. HMAC seal first, then mesh.

## Factory

Converge encapsulates the same engines (register, visibility, Linear, compose, settle).
Use it for dock / factory / big-bang only. Not the everyday consult path.
A Converge SETTLED loop may open a `task/*` PR when external writes are on.
Default is LOCAL_SETTLED. It never merges the user branch.

## This repo owns

| Repo | Owns | Must not |
| --- | --- | --- |
| seamwise | TaskPlan + lineage | Import Task-Spec. Write `tasks/`. Accept work |
| task-spec | Seal, eval, accept, TaskMesh | Cut seams. Merge the user branch |
| workhelm | Everyday RPI, backlog | Vendor the engines. Skip the HMAC seal |
| keep-spec | Picture, owned surfaces | Merge, approve, dispatch |
| brief-spec | Human handoff | Become a renderer other products import |
| converge | Factory compose + settlement | Become the everyday coordinator |
| customer | The product under `tasks/` | Invent a second backlog or a docs tree for hops |

## Merge rule

Never merge, push, or open a PR on the user branch unless a human asked.
TaskMesh `finish` prints a merge route. It does not merge.

## Stop

If the next hop is missing, fail closed. Do not skip ahead.
