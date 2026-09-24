import json
from dataclasses import asdict
from http.server import ThreadingHTTPServer
from pathlib import Path

from hermes_factory.metrics import compute_metrics
from hermes_factory.web import build_snapshot, create_server


class SnapshotClient:
    def __init__(self, tasks=None, runs=None, diagnostics=None, stats=None,
                 profiles=None, boards=None):
        self.tasks = tasks if tasks is not None else []
        self.runs = runs or {}
        self.diagnostics = diagnostics if diagnostics is not None else []
        self.stats = stats or {}
        self.profiles = {"default": True, "builder": True, "reviewer": True} if profiles is None else profiles
        self.boards = {"slug": "demo-rig"} if boards is None else boards
        self.calls: list[list[str]] = []

    def profile_exists(self, profile):
        return self.profiles.get(profile, False)

    def run_json(self, args):
        self.calls.append(args)
        sub = args[3] if len(args) > 3 else args[0]
        if sub == "list":
            return self.tasks
        if sub == "runs":
            return self.runs.get(args[4], [])
        if sub == "diagnostics":
            return self.diagnostics
        if sub == "stats":
            return self.stats
        raise AssertionError(f"unexpected: {args}")


class RigStub:
    def __init__(self, board_slug="demo-rig", seats=None, project_slug="demo",
                 board_name="Demo Rig"):
        self.board = type("Board", (), {"slug": board_slug, "name": board_name})
        self.project = type("Project", (), {"slug": project_slug})
        self.seats = seats or {}


def _rig():
    return RigStub(seats={"builder": type("Seat", (), {
        "profile": "builder", "role": "implementation", "skills": []})})


def test_build_snapshot_assembles_tasks_metrics_findings():
    tasks = [
        {"id": "t-1", "status": "done", "assignee": "builder",
         "created_at": 0, "completed_at": 3600, "result": None, "metadata": {}},
        {"id": "t-2", "status": "ready", "assignee": "builder",
         "created_at": 100, "completed_at": None, "metadata": {}},
    ]
    runs = {"t-1": [{"outcome": "review_requested"}, {"outcome": "changes_requested"}]}
    client = SnapshotClient(tasks=tasks, runs=runs,
                            diagnostics=[{"severity": "warning", "code": "stale-claim",
                                          "message": "done without proof"}],
                            stats={"now": 86400})
    snapshot = build_snapshot(_rig(), client)

    assert snapshot["board"] == "demo-rig"
    assert snapshot["board_name"] == "Demo Rig"
    assert [t["id"] for t in snapshot["tasks"]] == ["t-1", "t-2"]
    metrics = compute_metrics(tasks, runs, {"now": 86400})
    assert snapshot["metrics"] == asdict(metrics)
    assert len(snapshot["findings"]) == 2  # done-without-proof (t-1) + stale-claim
    assert snapshot["seats"] == {"builder": 1}
    assert snapshot["stats"] == {"now": 86400}
    assert snapshot["generated_at"] is not None


def test_build_snapshot_fetches_runs_only_for_done_tasks():
    tasks = [
        {"id": "t-1", "status": "done", "assignee": "builder", "metadata": {}},
        {"id": "t-2", "status": "ready", "assignee": "builder", "metadata": {}},
    ]
    client = SnapshotClient(tasks=tasks, runs={"t-1": [{"outcome": "success"}]})
    build_snapshot(_rig(), client)
    run_calls = [call for call in client.calls if call[3] == "runs"]
    assert [call[4] for call in run_calls] == ["t-1"]


def test_build_snapshot_open_work_counts_seats():
    tasks = [
        {"id": "t-1", "status": "ready", "assignee": "builder", "metadata": {}},
        {"id": "t-2", "status": "blocked", "assignee": "reviewer", "metadata": {}},
        {"id": "t-3", "status": "running", "assignee": "builder", "metadata": {}},
    ]
    client = SnapshotClient(tasks=tasks)
    snapshot = build_snapshot(_rig(), client)
    assert snapshot["seats"] == {"builder": 2, "reviewer": 1}


def test_build_snapshot_serializable():
    tasks = [{"id": "t-1", "status": "done", "assignee": "builder",
              "created_at": 0, "completed_at": 10, "result": "ok", "metadata": {}}]
    client = SnapshotClient(tasks=tasks)
    snapshot = build_snapshot(_rig(), client)
    text = json.dumps(snapshot, default=str)
    assert isinstance(text, str)


def test_create_server_serves_dashboard_and_data():
    import threading
    import urllib.request

    tasks = [{"id": "t-1", "status": "ready", "assignee": "builder", "metadata": {}}]
    client = SnapshotClient(tasks=tasks)
    server = create_server(lambda _slug: build_snapshot(_rig(), client), host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    assert isinstance(port, int) and port > 0

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page = urllib.request.urlopen(f"http://{host}:{port}/", timeout=5).read().decode()
        assert "Hermes Factory" in page
        assert "board" in page
        data = json.loads(urllib.request.urlopen(f"http://{host}:{port}/data", timeout=5).read())
        assert data["board"] == "demo-rig"
        assert data["tasks"]
        health = urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5).read()
        assert health == b"ok"
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_cli_parses_args():
    from hermes_factory.cli import build_parser

    args = build_parser().parse_args(
        ["dashboard", "--rig", str(Path("rig.yaml")), "--port", "8400"]
    )
    assert args.handler_key == "dashboard"
    assert args.port == 8400
    assert args.host == "127.0.0.1"
    assert args.refresh == 10
