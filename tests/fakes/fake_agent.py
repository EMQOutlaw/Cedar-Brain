"""Fake Claude/Codex CLI for tests. Behaviour is driven by directives in the prompt
(the backend strips the environment, so env vars cannot be used):

  FAKE_SLEEP=<seconds>   emit progress while sleeping
  FAKE_FAIL=<auth|rate|usage|network|crash>
  FAKE_FLOOD             write far more output than the backend accepts
  FAKE_GARBAGE           interleave malformed lines
  FAKE_JSON              final message is a JSON object
  FAKE_EMPTY             final message is empty
  FAKE_PLAN / FAKE_BADPLAN / FAKE_CYCLEPLAN   planner responses
  FAKE_CHILD=<pidfile>   spawn a long-lived child and record its pid
  FAKE_WRITE=<name>      write a file in the working directory
  FAKE_ARGV=<path>       record argv as JSON
"""
import json
import os
import re
import subprocess
import sys
import time

KIND = os.environ.get("FAKE_KIND") or ("codex" if "codex" in os.path.basename(sys.argv[0]) else "claude")
CLAUDE_HELP = """Usage: claude [options] [command] [prompt]
  -p, --print  --output-format <format>  --verbose  --permission-mode <mode> (choices: "acceptEdits", "dontAsk", "plan")
  --permission-prompts <target>  --tools <tools...>  --strict-mcp-config  --setting-sources <sources>  --model <model>
  --dangerously-skip-permissions  --session-id <uuid>"""
CODEX_HELP = """Run Codex non-interactively
  -s, --sandbox <SANDBOX_MODE>  [possible values: read-only, workspace-write, danger-full-access]
  -C, --cd <DIR>  --json  --color <COLOR>  --output-schema <FILE>  -m, --model <MODEL>
  --dangerously-bypass-approvals-and-sandbox"""


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def directive(prompt, name):
    m = re.search(rf"\b{name}(?:=(\S+))?", prompt)
    if not m:
        return None
    return m.group(1) or True


def plan(prompt):
    if directive(prompt, "FAKE_CYCLEPLAN"):
        return {"title": "cyclic", "summary": "", "steps": [
            {"id": "a", "description": "a", "executor": "git.status", "inputs": {}, "depends_on": ["b"],
             "expected_output": "x", "approval_required": False, "validation": "exit_status"},
            {"id": "b", "description": "b", "executor": "git.status", "inputs": {}, "depends_on": ["a"],
             "expected_output": "x", "approval_required": False, "validation": "exit_status"}]}
    if directive(prompt, "FAKE_BADPLAN"):
        return {"title": "bad", "steps": [{"id": "x", "description": "rm", "executor": "shell.exec",
                "inputs": {"cmd": "rm -rf /"}, "depends_on": [], "expected_output": "",
                "approval_required": False, "validation": "exit_status"}]}
    return {"title": "Inspect project", "summary": "Look at the repo, then summarise.", "steps": [
        {"id": "status", "description": "Check git state", "executor": "git.status", "inputs": {},
         "depends_on": [], "expected_output": "branch and cleanliness", "approval_required": False,
         "validation": "exit_status"},
        {"id": "summarise", "description": "Summarise the project", "executor": "agent.claude",
         "inputs": {"prompt": "Summarise this repository. FAKE_SLEEP=1"}, "depends_on": ["status"],
         "expected_output": "short summary", "approval_required": False, "validation": "non_empty_result"}]}


def main():
    args = sys.argv[1:]
    if args[:1] in (["--version"], ["-V"]):
        print("2.1.0 (Claude Code)" if KIND == "claude" else "codex-cli 0.160.0")
        return 0
    if KIND == "claude" and args[:1] == ["--help"]:
        print(CLAUDE_HELP)
        return 0
    if KIND == "codex" and args[:2] == ["exec", "--help"]:
        print(CODEX_HELP)
        return 0
    if KIND == "claude" and args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "fake", "email": "someone@example.com"}))
        return 0
    if KIND == "codex" and args[:2] == ["login", "status"]:
        print("Logged in using ChatGPT")
        return 0

    prompt = sys.stdin.read()
    if (p := directive(prompt, "FAKE_ARGV")):
        with open(p, "w") as fh:
            json.dump(sys.argv, fh)
    session = "fake-session-1234"
    if KIND == "claude":
        out({"type": "system", "subtype": "init", "session_id": session})
    else:
        out({"type": "thread.started", "thread_id": session})

    def message(text):
        if KIND == "claude":
            out({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, "session_id": session})
        else:
            out({"type": "item.completed", "item": {"type": "agent_message", "text": text}})

    if (fail := directive(prompt, "FAKE_FAIL")):
        msgs = {"auth": "Error: Not logged in. Please run /login", "rate": "API Error: 429 rate limit exceeded",
                "usage": "You have reached your usage limit", "network": "getaddrinfo ENOTFOUND api.example.com"}
        if fail == "crash":
            os.kill(os.getpid(), 9)
        sys.stderr.write(msgs.get(fail, "boom") + "\n")
        if KIND == "claude":
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": msgs.get(fail, "boom")})
        else:
            out({"type": "turn.failed", "error": {"message": msgs.get(fail, "boom")}})
        return 1
    if (pidfile := directive(prompt, "FAKE_CHILD")):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open(pidfile, "w") as fh:
            fh.write(str(child.pid))
    if directive(prompt, "FAKE_FLOOD"):
        chunk = "x" * 65536
        for _ in range(100000):
            sys.stdout.write(json.dumps({"type": "noise", "pad": chunk}) + "\n")
    if directive(prompt, "FAKE_GARBAGE"):
        sys.stdout.write("this is not json\n{broken\n[1,2]\n" + "y" * (2 * 1024 * 1024) + "\n")
        sys.stdout.flush()
    if (secs := directive(prompt, "FAKE_SLEEP")):
        end = time.time() + float(secs)
        while time.time() < end:
            message("working…")
            time.sleep(0.2)
    if (name := directive(prompt, "FAKE_WRITE")):
        with open(name, "w") as fh:
            fh.write("written by fake agent\n")

    if "You are Pathfinder" in prompt:
        final = json.dumps(plan(prompt))
    elif directive(prompt, "FAKE_JSON"):
        final = json.dumps({"answer": 42})
    elif directive(prompt, "FAKE_EMPTY"):
        final = ""
    else:
        final = "Fake analysis complete. token sk-ant-abcdefghijklmnop1234 should be redacted."
    message(final) if KIND == "codex" else None
    if KIND == "claude":
        out({"type": "result", "subtype": "success", "is_error": False, "result": final, "session_id": session,
             "usage": {"input_tokens": 10, "output_tokens": 5}})
    else:
        out({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
