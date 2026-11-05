import argparse

from harness.runtime import registry
from harness.runtime.supervisor import Supervisor


def test_registry_loads():
    reg = registry.load()
    assert len(reg.services) == 21
    assert [s.index for s in reg.services] == list(range(1, 22))
    assert len(reg.by_repo("commerce")) == 10 and len(reg.by_repo("ops")) == 11
    assert all(s.health == "/healthz" for s in reg.services)
    assert all(s.module for s in reg.by_repo("commerce"))
    assert all(s.path == f"services/{s.name}/server.js" for s in reg.by_repo("ops"))
    assert reg.get("edge-api").module == "edge_api"
    assert [s.name for s in reg.sims] == ["psp-sim", "partner-sim", "email-sim"]


def test_start_order():
    reg = registry.load()
    order = [s.name for s in reg.start_order()]
    assert order[0] == "bus"
    assert set(order[1:11]) == {s.name for s in reg.by_repo("ops")} - {"bus"}
    assert order[11] == "catalog"
    assert order[-1] == "edge-api"
    stages = [[s.name for s in st] for st in reg.stages()]
    assert stages[0] == ["bus"] and stages[2] == ["catalog"] and stages[-1] == ["edge-api"]
    assert [s.name for s in reg.resolve("commerce")][0] == "catalog"
    assert [s.name for s in reg.resolve("ops")][0] == "bus"
    assert [s.name for s in reg.resolve("cart")] == ["cart"]


def test_ports_and_urls():
    reg = registry.load()
    s = reg.get("catalog")
    assert (s.app_port(), s.mesh_port(), s.caller_port()) == (8102, 8202, 8202)
    assert reg.get("edge-api").caller_port(800) == 8800
    env = reg.url_env(0)
    assert env["CATALOG_URL"] == "http://127.0.0.1:8202"
    assert env["EDGE_API_URL"] == "http://127.0.0.1:8000"
    assert env["WAREHOUSE_URL"] == "http://127.0.0.1:8219"
    assert env["PSP_URL"] == "http://127.0.0.1:8090"
    assert env["PARTNER_WEBHOOK_URL"] == "http://127.0.0.1:8091/hooks"
    assert env["EMAIL_URL"] == "http://127.0.0.1:8092"


def _sup(tmp_path, monkeypatch, offset=800):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("LARDER_SERVICE_CMD_OVERRIDE", raising=False)
    # the supervisor writes these into os.environ; register them so monkeypatch restores them
    for k in ("LARDER_ADMIN_TOKEN", "LARDER_RUN_ID", "LARDER_PORT_OFFSET", "LARDER_ARTIFACTS_DIR"):
        monkeypatch.setenv(k, "t")
    args = argparse.Namespace(
        workspace=str(tmp_path / "ws"), artifacts=str(tmp_path / "art"), run_id="r1", port_offset=offset,
        env_file=None, handover_at=None, duration_min=None, eval=False,
    )
    return Supervisor(args)


def test_service_env_contains_contract(tmp_path, monkeypatch):
    sup = _sup(tmp_path, monkeypatch)
    reg = registry.load()
    ws = tmp_path / "ws"
    for svc in reg.services:
        p = sup.procs[svc.name]
        env = p.env
        for other in reg.services:
            assert env[other.env_var] == f"http://127.0.0.1:{other.caller_port(800)}"
        assert env["EDGE_API_URL"] == "http://127.0.0.1:8800"
        assert env["PSP_URL"] == "http://127.0.0.1:8890"
        assert env["PARTNER_WEBHOOK_URL"] == "http://127.0.0.1:8891/hooks"
        assert env["EMAIL_URL"] == "http://127.0.0.1:8892"
        assert env["PORT"] == str(8900 + svc.index)
        assert env["LARDER_SERVICE"] == svc.name
        assert env["LARDER_RUN_ID"] == "r1"
        assert env["OTEL_SERVICE_NAME"] == svc.name
        assert f"larder.repo={svc.repo}" in env["OTEL_RESOURCE_ATTRIBUTES"]
        assert f"service.name={svc.name}" in env["OTEL_RESOURCE_ATTRIBUTES"]
        assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
        assert "LARDER_ADMIN_TOKEN" not in env and "LARDER_ARTIFACTS_DIR" not in env
        if svc.repo == "commerce":
            assert env["LARDER_DATA_DIR"] == str(ws / "commerce" / "var" / svc.name)
            assert p.cmd == [str(ws / "commerce" / ".venv" / "bin" / "python"), "-m", f"services.{svc.module}"]
            assert p.cwd == ws / "commerce"
        else:
            assert env["LARDER_DATA_DIR"] == str(ws / "ops" / "data" / svc.name)
            assert p.cmd == ["node", "--require", "./lib/telemetry/otel.js", f"services/{svc.name}/server.js"]
            assert p.cwd == ws / "ops"
        assert p.health_url == f"http://127.0.0.1:{8900 + svc.index}/healthz"
        assert p.log_paths[0] == ws / ".larder" / "logs" / f"{svc.name}.log"
    st = sup.status()
    assert len(st["services"]) == 21
    assert st["services"]["warehouse"]["mesh_port"] == 9019
    assert set(st["infra"]) == {"psp-sim", "partner-sim", "email-sim", "mesh"}
    assert st["groups"]["ops"][0] == "bus" and st["groups"]["commerce"][-1] == "edge-api"


def test_cmd_override(tmp_path, monkeypatch):
    sup = _sup(tmp_path, monkeypatch)
    monkeypatch.setenv("LARDER_SERVICE_CMD_OVERRIDE", "python3 /tmp/stub.py {name} {port} {repo}")
    assert sup.service_cmd(registry.load().get("tax")) == ["python3", "/tmp/stub.py", "tax", "8917", "ops"]
