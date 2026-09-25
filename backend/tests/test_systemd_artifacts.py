from __future__ import annotations

from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]


def _load_cutover_module(name: str):
    script = ROOT / "scripts/verify_production_cutover.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_waterfallhunter_systemd_unit_contract() -> None:
    text = (ROOT / "deploy/systemd/waterfallhunter.service").read_text(encoding="utf-8")
    assert "After=docker.service network-online.target" in text
    assert "Requires=docker.service" in text
    assert "WorkingDirectory=/srv/waterfallhunter/app" in text
    assert "RemainAfterExit=yes" in text
    assert "scripts/production_compose.sh up -d --remove-orphans" in text
    assert "scripts/production_compose.sh stop --timeout 30" in text
    assert "down -v" not in text


def test_healthcheck_timer_is_bounded_and_persistent() -> None:
    service = (ROOT / "deploy/systemd/waterfallhunter-healthcheck.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy/systemd/waterfallhunter-healthcheck.timer").read_text(encoding="utf-8")
    assert "scripts/healthcheck_simple.py" in service
    assert "verify_production_cutover.py --health-only --recover" not in service
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert "Restart=always" not in service


def test_nginx_template_exposes_only_frontend_loopback() -> None:
    text = (ROOT / "deploy/nginx/waterfallhunter.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:3000;" in text
    assert text.count("proxy_pass http://127.0.0.1:8000;") == 3
    for endpoint in ("/api/health", "/livez", "/readyz"):
        assert f"location = {endpoint}" in text
    assert "127.0.0.1:9090" not in text
    assert "127.0.0.1:3001" not in text


def test_compose_accepts_host_owned_environment_file() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${WFH_ENV_FILE:-.env}" in text


def test_health_recovery_has_finite_budget_and_cooldown() -> None:
    text = (ROOT / "scripts/verify_production_cutover.py").read_text(encoding="utf-8")
    assert "FAILURES_BEFORE_RECOVERY = 3" in text
    assert "RECOVERY_COOLDOWN_SECONDS = 600" in text
    assert "MAX_RECOVERIES_PER_HOUR = 3" in text


def test_systemd_and_recovery_reuse_optional_host_owned_production_volume_override() -> None:
    wrapper = ROOT / "scripts/production_compose.sh"
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o100
    text = wrapper.read_text(encoding="utf-8")
    verifier = (ROOT / "scripts/verify_production_cutover.py").read_text(encoding="utf-8")
    override = "/srv/waterfallhunter/runtime/production-volumes.override.yml"
    assert override in text
    assert 'if [[ -f "$OVERRIDE" ]]' in text
    assert "--project-name waterfallhunter" in text
    assert "--env-file" in text
    assert "production-volumes.override.yml" in verifier


def test_release_certificate_builder_is_exact_sha_and_signal_only() -> None:
    module = _load_cutover_module("verify_production_cutover_test")
    snapshot = {
        "healthy": True,
        "running_revision": "a" * 40,
        "core_revisions": {
            "waterfall-backend": "a" * 40,
            "waterfall-frontend": "a" * 40,
            "waterfall-watchdog": "a" * 40,
        },
        "checkout_revision": "a" * 40,
        "live_trading_enabled": False,
        "backend_endpoints": {"/livez": True, "/readyz": True, "/healthz": True},
        "notification_delivery_ready": True,
        "notification_delivery": {
            "healthy": True,
            "transport": {
                "configured": True,
                "worker_running": True,
                "probe": {
                    "reachable": True,
                    "bot_reachable": True,
                    "chat_reachable": True,
                },
            }
        },
    }
    certificate = module.build_release_certificate(snapshot, generated_at=123)
    assert certificate["certificate_type"] == "waterfallhunter_release_v1"
    assert certificate["status"] == "PASS"
    assert certificate["release_sha"] == "a" * 40
    assert certificate["production_healthy"] is True
    assert certificate["live_trading_enabled"] is False
    assert len(certificate["certificate_sha256"]) == 64


def test_release_certificate_builder_rejects_unhealthy_or_revision_mismatch() -> None:
    module = _load_cutover_module("verify_production_cutover_invalid_test")
    base = {
        "healthy": True,
        "running_revision": "a" * 40,
        "core_revisions": {
            "waterfall-backend": "a" * 40,
            "waterfall-frontend": "a" * 40,
            "waterfall-watchdog": "a" * 40,
        },
        "checkout_revision": "b" * 40,
        "live_trading_enabled": False,
        "backend_endpoints": {"/livez": True, "/readyz": True, "/healthz": True},
    }
    try:
        module.build_release_certificate(base, generated_at=123)
    except ValueError:
        pass
    else:
        raise AssertionError("revision mismatch must refuse release certification")


def test_release_certificate_builder_rejects_core_container_revision_mismatch() -> None:
    module = _load_cutover_module("verify_production_cutover_core_revision_test")
    snapshot = {
        "healthy": True,
        "running_revision": "a" * 40,
        "core_revisions": {
            "waterfall-backend": "a" * 40,
            "waterfall-frontend": "b" * 40,
            "waterfall-watchdog": "a" * 40,
        },
        "checkout_revision": "a" * 40,
        "live_trading_enabled": False,
        "backend_endpoints": {"/livez": True, "/readyz": True, "/healthz": True},
    }
    try:
        module.build_release_certificate(snapshot, generated_at=123)
    except ValueError:
        pass
    else:
        raise AssertionError("all core runtime OCI revisions must match the certified checkout")


def test_production_verifier_rejects_noncanonical_operator_paths() -> None:
    module = _load_cutover_module("verify_operator_path_test")
    try:
        module._require_canonical_operator_path(
            Path("/tmp/not-production"), module.CANONICAL_PROJECT_DIR
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Production verifier must reject alternate project paths")


def test_recovery_state_io_uses_configured_canonical_state_path(tmp_path: Path) -> None:
    module = _load_cutover_module("verify_state_path_test")
    module.CANONICAL_RUNTIME_DIR = tmp_path
    module.CANONICAL_STATE_FILE = tmp_path / "healthcheck-state.json"
    state = {"consecutive_failures": 2, "recoveries": [], "last_recovery_at": 0.0}
    module._save_state(state)
    assert module.CANONICAL_STATE_FILE.exists()
    assert module._load_state() == state


def test_recovery_guard_refuses_lock_held_by_deployment(tmp_path: Path) -> None:
    import fcntl
    import importlib.util
    import os
    script = ROOT / "scripts/verify_production_cutover.py"
    spec = importlib.util.spec_from_file_location("verify_recovery_lock_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CANONICAL_RUNTIME_DIR = tmp_path
    module.CANONICAL_DEPLOY_LOCK_FILE = tmp_path / "deploy.lock"
    fd = os.open(module.CANONICAL_DEPLOY_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert module._try_acquire_deploy_guard() is None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_health_compose_exports_canonical_wfh_env_file(monkeypatch) -> None:
    module = _load_cutover_module("verify_compose_env_test")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        return Result()

    monkeypatch.setattr(module, "_compose_files", lambda project_dir: [str(project_dir / "docker-compose.yml")])
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    env_file = Path("/etc/waterfallhunter/waterfallhunter.env")
    module._compose(Path("/srv/waterfallhunter/app"), env_file, "ps")

    assert captured["env"]["WFH_ENV_FILE"] == str(env_file)


def test_recovery_path_skips_compose_while_deployment_lock_is_busy(tmp_path: Path, monkeypatch) -> None:
    module = _load_cutover_module("verify_recovery_busy_test")
    module.CANONICAL_RUNTIME_DIR = tmp_path
    module.CANONICAL_STATE_FILE = tmp_path / "healthcheck-state.json"
    module._save_state({
        "consecutive_failures": 2,
        "recoveries": [],
        "last_recovery_at": 0.0,
    })
    monkeypatch.setattr(module, "_try_acquire_deploy_guard", lambda: None)

    def compose_must_not_run(*args, **kwargs):
        raise AssertionError("recovery compose must not run during deployment")

    monkeypatch.setattr(module, "_compose", compose_must_not_run)
    ok, reason = module.maybe_recover(
        module.CANONICAL_PROJECT_DIR,
        module.CANONICAL_ENV_FILE,
        {"healthy": False},
    )
    assert ok is False
    assert reason == "deployment_in_progress"


def test_recovery_releases_deploy_guard_after_failed_compose(tmp_path: Path, monkeypatch) -> None:
    module = _load_cutover_module("verify_recovery_release_test")
    module.CANONICAL_RUNTIME_DIR = tmp_path
    module.CANONICAL_STATE_FILE = tmp_path / "healthcheck-state.json"
    module._save_state({
        "consecutive_failures": 2,
        "recoveries": [],
        "last_recovery_at": 0.0,
    })
    released = []
    monkeypatch.setattr(module, "_try_acquire_deploy_guard", lambda: 77)
    monkeypatch.setattr(module, "_release_deploy_guard", released.append)

    class Result:
        returncode = 1

    monkeypatch.setattr(module, "_compose", lambda *args, **kwargs: Result())
    ok, reason = module.maybe_recover(
        module.CANONICAL_PROJECT_DIR,
        module.CANONICAL_ENV_FILE,
        {"healthy": False},
    )
    assert ok is False
    assert reason == "recovery_command_failed"
    assert released == [77]


def test_required_service_without_healthcheck_is_unhealthy(monkeypatch) -> None:
    module = _load_cutover_module("verify_missing_healthcheck_test")

    class ComposeResult:
        returncode = 0
        stdout = "abc123def456\n"
        stderr = ""

    class InspectResult:
        returncode = 0
        stdout = "running|none\n"
        stderr = ""

    monkeypatch.setattr(module, "_compose", lambda *args, **kwargs: ComposeResult())
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: InspectResult())
    ok, status = module._service_health(
        module.CANONICAL_PROJECT_DIR,
        module.CANONICAL_ENV_FILE,
        "waterfall-backend",
    )
    assert ok is False
    assert status == "missing_healthcheck"


def test_release_evidence_requires_notification_delivery_health(monkeypatch) -> None:
    module = _load_cutover_module("verify_notification_release_test")
    sha = "a" * 40
    monkeypatch.setattr(module, "health_snapshot", lambda *args: {"healthy": True})
    monkeypatch.setattr(module, "_backend_endpoint", lambda *args: True)
    monkeypatch.setattr(module, "_running_revisions", lambda: {name: sha for name in module.CORE_CONTAINERS})
    monkeypatch.setattr(module, "_checkout_revision", lambda *args: sha)
    monkeypatch.setattr(module, "_live_trading_enabled", lambda *args: False)
    monkeypatch.setattr(
        module,
        "_notification_delivery_snapshot",
        lambda *args: {
            "transport": {
                "configured": True,
                "worker_running": False,
                "probe": {"reachable": False, "bot_reachable": True, "chat_reachable": False},
            }
        },
    )
    snapshot = module.release_evidence_snapshot(
        module.CANONICAL_PROJECT_DIR,
        module.CANONICAL_ENV_FILE,
    )
    assert snapshot["healthy"] is False
    assert snapshot["notification_delivery_ready"] is False


def test_health_verifier_uses_absolute_git_binary() -> None:
    module = _load_cutover_module("verify_production_cutover_git_bin")
    assert module.GIT_BIN == "/usr/bin/git"
