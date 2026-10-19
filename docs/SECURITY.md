# Security model and limitations

## Trust boundary

* **Transport.** Unix socket only. No TCP port, no HTTP. The directory is `0700`, the socket `0600`, and every connection is checked with `SO_PEERCRED`: a peer whose uid differs from the server's is dropped.
* **What that protects.** It stops other local users. It does **not** isolate the backend from other processes running as the same user. Any same-user process can connect and do whatever the protocol allows, which is no more than it could already do by running `claude` or `codex` itself.
* **Authority.** The backend validates every request and enforces every rule below. The frontend is never trusted to enforce anything.

## Agent execution

* Processes start with `asyncio.create_subprocess_exec` (argv, never a shell). The prompt is written to **stdin**, so it never appears in `ps` and is never interpolated into a command line.
* Each job runs in its own session/process group. Cancel and timeout send SIGTERM to that group, then SIGKILL after 5 s. pid and `/proc` start time are recorded, and crash recovery kills a leftover process only when both match, so an unrelated process that reused the pid is never touched.
* `--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox` and similar flags are refused by the argv builder even if configuration asks for them. A test covers this.
* **Claude** runs read-only: `--permission-mode dontAsk --tools Read,Grep,Glob --permission-prompts none --strict-mcp-config`. `claude -p` has no OS-level sandbox, so Claude is **never** offered write mode (`agents.claude.write = unavailable`).
* **Codex** runs read-only with `--sandbox read-only`. Write mode uses `--sandbox workspace-write -C <worktree>`, where the worktree is a fresh `git worktree` on a new branch. On Linux, Codex enforces its sandbox at the OS level. That was verified on this machine on 2026-10-18: a write outside the allowed root failed with `Read-only file system`. Write steps must require approval and never run without a `runs.approve`.
* A working directory alone is **not** treated as a sandbox.
* Nothing ever merges, pushes, switches branches, changes remotes or runs `git init`. Worktrees are kept for human review.
* At most `max_agent_processes` agent processes run at once (default 2). A partial unique index allows one active job per owner (run step / plan) and one active run per workflow.
* Output is bounded: lines up to 1 MiB, 32 MiB of stdout and 4 MiB of stderr per job (the job is terminated beyond that), log files capped at 8 MiB, results at 256 KiB, and at most 400 progress events per job.

## Credentials

* Agents use the CLIs' own login. Child processes get a minimal environment: an allowlist of locale, path and XDG variables, plus that agent's own auth/config variables **only if they already exist** in the server's environment (`ANTHROPIC_API_KEY`/`CLAUDE_CONFIG_DIR` for Claude, `OPENAI_API_KEY`/`CODEX_HOME` for Codex). Other secrets in the environment (cloud keys, GitHub tokens) are not passed through.
* The database schema has no credential columns, and a test enforces that. `auth status` probes keep only the logged-in boolean and method name.
* Output, logs, events and stored results go through regex redaction for common token formats (Anthropic/OpenAI/GitHub/GitLab/Slack/AWS/Google keys, JWTs, bearer headers, private keys, `password=`/`api_key=`-style pairs). Redaction is a safety net, not a guarantee. The patterns are linear-time, so a hostile line cannot stall the server; a test covers this too.

## Skills

* Discovery and import only read files: nothing is executed, installed or sourced. Script files are flagged (`script: true`, `has_scripts`) so the UI can show them.
* `SKILL.md` must be a regular file of at most 256 KiB, valid UTF-8, with no NUL bytes. A symlinked `SKILL.md` is invalid.
* A skill directory reached through a symbolic link is accepted only if it resolves inside an approved root and outside protected locations (`~/.ssh`, `~/.gnupg`, `~/.aws`, browser profiles, CLI credential files, `/etc`, `/proc`…). Symlinked files inside a skill are listed with `symlink_safe`.
* Every skill is `untrusted`. Trust resets automatically whenever the content hash changes. v1 has no method that raises trust.
* Skill text appears in planner prompts only as name and description, inside a block marked as untrusted data.

## Paths

Every path check runs on `realpath`-resolved paths, so `..` and symlink escapes are caught. Graph `source_file` references outside the project resolve to `path: null`. Workflow inputs with a `path` must stay inside the project.

## Known limitations

* Same-user processes are trusted, as described above.
* Redaction is pattern-based.
* Codex's sandbox is Codex's own implementation. The backend relies on it and does not add its own (no bubblewrap/landlock wrapper in v1).
* A write step interrupted by a crash may leave a half-edited worktree. It is reported as failed with `INTERRUPTED` and never retried.
* Planner output from the model is only a proposal. Its validation catches structural and policy violations, not plans that are well-formed but unwise. The user reviews the plan, approves the run, and approves each write step.
