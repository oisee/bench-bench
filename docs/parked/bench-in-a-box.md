# Bench in a box (parked)

Status: design only, parked on 2026-10-25. Nothing below is built, except the endpoint-key fix in step 1.

**The idea.** `docker compose up` serves a page on localhost. A person enters an endpoint, a key and a model, picks a
tier (smoke / medium / full) and tasks, and presses Run. The page shows the run live: task, agent steps, timer and
verdict. At the end it shows time, time to first green, requests and tokens/s. A button submits the results. The
same works without the page: `docker compose run bench --endpoint … --tier smoke`, then `… submit`.

## 1. Sandbox

Keep bwrap and `bin/sandbox.sh` unchanged inside one container, so the protocol stays the same. The container
starts with a seccomp profile that allows `unshare`/`clone(CLONE_NEWUSER)`, plus `apparmor=unconfined`. It does not
use `--privileged`, `CAP_SYS_ADMIN` or the docker socket. If user namespaces are unavailable, it refuses to run
rather than run unsandboxed.

"The container is the sandbox" breaks the protocol. The agent, the grader, the task cache and the key would share
one filesystem, and `.cache/repos` holds the MERGE commit, that is, the hidden tests. Separate containers per run
need the docker socket in the orchestrator, which amounts to root on the host. Rootless podman is a later option.

To check: Ubuntu 24.04 hosts restrict user namespaces through AppArmor; Docker Desktop runs a Linux VM.

## 2. Endpoint key

Done for `provider: custom` (the endpoint key fix):

- the sandbox gets a dummy key;
- a proxy outside the sandbox adds the real key;
- `bin/secret-scan.py` searches the run directory and sets `secret_leak`.

Keys of the hosted providers (Z.ai, Alibaba, Azure, Anthropic/OpenAI logins) still enter the sandbox. A box for
outside users would accept `custom` only, or route the hosted providers through the same proxy. Before submit, the
scan runs over the whole bundle.

## 3. Submit (no server of ours)

- **A, with gh in the container.** `gh auth login --device`, then fork, then a PR containing only small files:
  `results/<executor>/<task>/<run>/` with `patch.diff`, `result.json`, `meta.json`, `first-green.json` and a traffic
  summary. The raw `traffic.jsonl` and `snap.git` (tens to hundreds of MB on L3) go as a `.tar.zst` release asset
  in the user's own fork, linked from the PR.
- **B, fallback without gh.** Write `bundle.tar.zst` and a prefilled "open PR" link with instructions.

## 4. Trust

Results are self-reported. Regrading the patches in CI is necessary but not sufficient: the hidden tests come from
public upstream PRs, so someone could submit the upstream fix as model output.

- **CI regrade.** It runs on `pull_request`, not `pull_request_target`, with no secrets and a read-only token.
- **Consistency.** The patch must be reproducible from the Edit/Write tool calls in the traffic, and the snapshots
  must follow the traffic order. The existing leak audit for upstream URLs also runs.
- **Leaderboard.** A separate "community, self-run" section. Only runs that were regraded and passed the
  consistency check go there, with time as reported and hardware as declared. They never mix into our tables.

## 5. Image

- **Base image**, about 1.5 GB: Go 1.26, Node 22, codex, pi, the bench scripts and the UI. Claude Code is
  proprietary: install it with npm on first run and don't redistribute it in the image. codex is Apache-2.0;
  check pi's license.
- **Task data**, measured on one machine: bare repos 1.8 GB and templates 1.8 GB. The three osg templates take
  about 480 MB each, mostly `node_modules`. Add the Go module cache for the tasks.
- **Proposal.** A data image for SMOKE (L1a + L2b, about 0.6 GB); `bin/setup.sh` on demand for the other tasks,
  10–20 minutes with GitHub access.

## UI

A standard-library Python server with server-sent events. The form takes the endpoint, the key (kept in memory
only), the model, the harness and the tier/tasks. The live view reads `live.txt` and `traffic.jsonl`. The summary
comes from `result.json`, `first-green.json` and `bin/traffic-summary.py`. The command-line path uses the same
backend.

## Estimate (working days)

| Part | Days |
|---|---|
| Endpoint key fix (done for `custom`) | 1 |
| Image, compose, seccomp profile, first-run setup; Linux and Docker Desktop | 2–3 |
| CLI path, tiers for the 6 tasks, bundle | 1 |
| Web UI with live view | 2–3 |
| Submit A and B | 1–1.5 |
| CI regrade, traffic consistency check, community section | 2 |
| Critic rounds and docs | 1 |
| **Total** | **10–12** |

**MVP**, about 5 days: the key fix, a CLI-only image, bundle B and the CI regrade.

## Open questions

- Which tasks make up the SMOKE tier for the current six? The proposal is L1a + L2b.
- Do community results appear on the main page?
- Claude Code licensing inside a distributed image.
