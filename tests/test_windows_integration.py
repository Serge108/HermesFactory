import json
import sys

import pytest

from hermes_factory.hermes_cli import HermesClient

_DUMPER_SCRIPT = """\
import json, sys
print(json.dumps(sys.argv[1:]))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process contract")
def test_windows_path_with_spaces_reaches_child_as_one_argument(tmp_path):
    workspace = tmp_path / "Path With Spaces"
    workspace.mkdir()
    script = workspace / "dump_argv.py"
    script.write_text(_DUMPER_SCRIPT, encoding="utf-8")

    client = HermesClient(executable=sys.executable, timeout_seconds=60)
    output = client.run([str(script), "F:/Path With Spaces"])

    assert json.loads(output) == ["F:/Path With Spaces"]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process contract")
def test_windows_cwd_with_spaces_reaches_child(tmp_path):
    workspace = tmp_path / "Path With Spaces"
    workspace.mkdir()
    client = HermesClient(executable=sys.executable, timeout_seconds=60)
    script = workspace / "dump_argv.py"
    script.write_text("import sys; print(sys.platform)", encoding="utf-8")

    output = client.run([str(script)], cwd=workspace)

    assert output.strip() == "win32"
