#!/usr/bin/env python3
"""Run the real Quickshell renderer against authoritative fixtures, without sockets."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(os.environ.get("MYCELIUM_EVIDENCE_OUT", str(ROOT / "build/frontend-evidence")))
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="mycelium-test-") as temporary:
    temp = Path(temporary)
    cockpit = temp / "cockpit"
    shutil.copytree(ROOT / "cockpit", cockpit)
    shutil.copy(ROOT / "tests/frontend/Smoke.qml", cockpit / "Smoke.qml")
    runtime = temp / "runtime"
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(temp / "config"),
               QT_QPA_PLATFORM="offscreen", QT_QUICK_CONTROLS_STYLE="Basic", QT_QPA_PLATFORMTHEME="generic",
               QT_QUICK_BACKEND="software", CEDAR_BRAIN_FIXTURES="1", MYCELIUM_EVIDENCE=str(EVIDENCE),
               MYCELIUM_FIXTURE_FILE=str(ROOT / "backend/cedar_brain/fixtures/responses.json"))
    env.pop("WAYLAND_DISPLAY", None)
    result = subprocess.run(["qs", "-p", str(cockpit / "Smoke.qml"), "--no-color"], env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    (EVIDENCE / "smoke.log").write_text(result.stdout)
    print(result.stdout)
    assert result.returncode == 0, result.returncode
    assert "FRONTEND_PASS" in result.stdout and "FRONTEND_FAIL" not in result.stdout
    errors = [line for line in result.stdout.splitlines() if any(x in line for x in
              ("TypeError", "ReferenceError", "Unable to assign", "Binding loop", "Failed to load", "is not a type", "Cannot assign", "QML Settings", "WARN scene:", "Unexpected token", "Type AgentCard unavailable", "Type WorkflowStep unavailable"))]
    assert not errors, "\n".join(errors)
    print("Frontend runtime and fixture checks passed.")
