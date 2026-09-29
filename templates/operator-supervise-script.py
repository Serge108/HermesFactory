"""Observer script template for the external Factory operator.

Install it in the active Hermes profile's ``scripts`` directory (the profile path
comes from ``hermes config path``) and register it as a no-agent cron job, one per
project:

    hermes cron create "1m" \
      --name "Hermes Factory operator: <project>" \
      --script "<this-file>" \
      --no-agent \
      --workdir "F:\\HermesFactory"

Replace FACTORY_PROJECT, FACTORY_PROMPT, and the paths below with the project's
values. The names matter: the job name must start with ``Hermes Factory operator:``
so the supervisor never binds it to its own project.

Behaviour: the script is silent unless it started an operator session. It exits
non-zero only for a real fault: an unreadable cron evidence source, or a project
with no bound watchdog job at all, which is a misconfiguration of this script and
must not look like a quiet healthy loop.
"""

import json
import subprocess
import sys

FACTORY_PROJECT = "FACTORY_PROJECT"
FACTORY_PROMPT = r"F:\HermesFactory\templates\operator-session-prompt.md"

COMMAND = [
    r"C:\Users\Hare Krishna\AppData\Local\hermes\bin\uv.exe",
    "run",
    "--project",
    r"F:\HermesFactory",
    "hermes-factory",
    "operator",
    "supervise",
    "--project",
    FACTORY_PROJECT,
    "--profile",
    "default",
    "--prompt",
    FACTORY_PROMPT,
    "--repo",
    r"F:\HermesFactory",
    "--json",
]

completed = subprocess.run(
    COMMAND,
    cwd=r"F:\HermesFactory",
    text=True,
    encoding="utf-8",
    capture_output=True,
    check=False,
)
if completed.returncode != 0:
    detail = completed.stderr.strip() or completed.stdout.strip() or str(completed.returncode)
    print(detail, file=sys.stderr)
    raise SystemExit(completed.returncode)

payload = json.loads(completed.stdout)
if payload["verdict"] == "unregistered":
    print(
        f"no watchdog job is bound to {FACTORY_PROJECT}: the operator observer cannot "
        "see the loop it supervises",
        file=sys.stderr,
    )
    raise SystemExit(3)

if payload["launched"]:
    print(
        json.dumps(
            {
                "verdict": payload["verdict"],
                "reason": payload["reason"],
                "project": payload["project"],
                "lock": payload["lock"],
                "log": payload["log"],
            },
            ensure_ascii=False,
        )
    )
