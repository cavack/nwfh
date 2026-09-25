from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import json
import sys
import time
from pathlib import Path

import yaml

from waterfallhunter.core.contracts import ExecutionMode, SignalDecisionPacket
from waterfallhunter.core.signal_metadata import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-production.yml"
ACTIONLINT_CONFIG = ROOT / ".github" / "actionlint.yaml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_production.sh"
SELF_HOSTED_DIR = ROOT / "deploy" / "self-hosted"
PRIVILEGED_WRAPPER = SELF_HOSTED_DIR / "wfh-production-deploy"
HOST_INSTALLER = SELF_HOSTED_DIR / "install-production-runner-host.sh"
RUNNER_PROVISIONER = SELF_HOSTED_DIR / "provision-ephemeral-runner.sh"
SCAN_EXCLUDED_PARTS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _tracked_text_files() -> list[Path]:
    git = shutil.which("git")
    if git is not None:
        result = subprocess.run(
            [git, "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        return [ROOT / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]

    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in SCAN_EXCLUDED_PARTS for part in path.parts)
    )


def _main_deploy_sequence(text: str) -> str:
    return text.split('[[ "$WFH_DEPLOY_SHA"', maxsplit=1)[1]


def test_execution_mode_is_signal_only() -> None:
    deprecated_mode = "SIMULATED" + "_ONLY"
    assert ExecutionMode.SIGNAL_ONLY.value == "SIGNAL_ONLY"
    assert deprecated_mode not in {member.value for member in ExecutionMode}


def test_signal_decision_defaults_to_signal_only() -> None:
    field = SignalDecisionPacket.model_fields["execution_mode"]
    assert field.default is ExecutionMode.SIGNAL_ONLY


def test_tracked_repository_text_does_not_use_deprecated_product_boundary_terms() -> None:
    """Reject deprecated execution-boundary language from tracked repository text."""
    forbidden = (
        "SIMULATED" + "_ONLY",
        "simulated" + "-only",
        "Simulated" + "-only",
        "simulated" + " trading",
        "Simulated" + " trading",
        "simulated" + "-trading",
        "Simulated" + "-trading",
    )
    deprecated_word = "pa" + "per"
    standalone_deprecated = re.compile(
        rf"(?i)(?<![A-Za-z0-9_]){deprecated_word}(?![A-Za-z0-9_])"
    )
    offenders: list[str] = []
    binary_suffixes = {".pyc", ".map", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}
    for path in _tracked_text_files():
        if path.suffix in binary_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(term in text for term in forbidden) or standalone_deprecated.search(text):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_production_deploy_requires_explicit_main_dispatch_after_ci() -> None:
    """Require the manual Production gate to be structurally bound to protected main."""
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    deploy_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    ci_workflow = yaml.load(ci_text, Loader=yaml.BaseLoader)

    assert isinstance(ci_workflow, dict)
    triggers = ci_workflow["on"]
    deploy_input = triggers["workflow_dispatch"]["inputs"]["deploy_production"]
    assert deploy_input["required"] == "true"
    assert deploy_input["type"] == "boolean"
    assert deploy_input["default"] == "false"

    deploy_job_contract = ci_workflow["jobs"]["deploy-production"]
    assert deploy_job_contract["needs"] == [
        "backend",
        "frontend",
        "dependency-audit",
        "container-validation",
        "repository-hygiene",
    ]
    assert deploy_job_contract["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/main' && inputs.deploy_production == true }}"
    )
    assert deploy_job_contract["uses"] == "./.github/workflows/deploy-production.yml"

    assert "workflow_call:" in deploy_text
    assert "workflow_run:" not in deploy_text
    assert "workflow_dispatch" not in deploy_text
    assert "environment: production" in deploy_text
    assert "WFH_DEPLOY_SHA: ${{ github.sha }}" in deploy_text

    callers = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        if "uses: ./.github/workflows/deploy-production.yml" in workflow.read_text(encoding="utf-8"):
            callers.append(workflow.name)
    assert callers == ["ci.yml"]



def test_production_deploy_uses_ephemeral_self_hosted_runner_without_ssh() -> None:
    workflow = yaml.load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    deploy = workflow["jobs"]["deploy"]
    assert deploy["runs-on"] == ["self-hosted", "linux", "x64", "wfh-production-${{ github.run_id }}-${{ github.run_attempt }}"]
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "WFH_PROD_SSH_KEY",
        "WFH_DEPLOY_SSH_KEY",
        "WFH_PROD_KNOWN_HOSTS",
        "WFH_DEPLOY_KNOWN_HOSTS",
        "ssh \\",
        "scp \\",
        "StrictHostKeyChecking",
    ):
        assert forbidden not in text
    assert "sudo /usr/local/sbin/wfh-production-deploy" in text
    assert "${{ runner.temp }}" in text


def test_actionlint_knows_the_custom_production_runner_label() -> None:
    """Keep workflow lint aware of the narrow self-hosted runner label."""
    config = yaml.safe_load(ACTIONLINT_CONFIG.read_text(encoding="utf-8"))
    assert config["self-hosted-runner"]["labels"] == ["wfh-production-*"]


def test_self_hosted_runner_host_contract_is_ephemeral_and_narrowly_privileged() -> None:
    assert PRIVILEGED_WRAPPER.is_file()
    assert HOST_INSTALLER.is_file()
    assert RUNNER_PROVISIONER.is_file()
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    installer = HOST_INSTALLER.read_text(encoding="utf-8")
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert "release-recovery-gate.json" in wrapper
    assert "RUNTIME_ROOT=\"/srv/waterfallhunter/runtime\"" in wrapper
    assert "READY_FOR_EXPLICIT_DISPATCH" in wrapper
    assert "EXPLICIT_WORKFLOW_DISPATCH" in wrapper
    assert "wfh-deploy" in wrapper
    assert "valid_until" in wrapper
    assert "300" in wrapper
    assert "/usr/local/sbin/wfh-production-deploy" in installer
    assert "/usr/local/sbin/wfh-provision-production-runner" in installer
    assert "/etc/waterfallhunter/runner-boundary-revision" in installer
    assert "/etc/sudoers.d/wfh-production-deploy" in installer
    assert "visudo -cf" in installer
    assert "NOPASSWD" in installer
    assert "--ephemeral" in provisioner
    assert "--unattended" in provisioner
    assert "wfh-production" in provisioner
    assert "registration-token" in provisioner
    assert "sha256" in provisioner.lower()
    assert "svc.sh install" not in provisioner


def test_runner_release_version_url_and_digest_are_pinned_together() -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert 'RUNNER_VERSION="2.337.0"' in provisioner
    assert (
        'RUNNER_ASSET="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"'
        in provisioner
    )
    assert (
        'RUNNER_ASSET_URL="https://github.com/actions/runner/releases/download/'
        'v${RUNNER_VERSION}/${RUNNER_ASSET}"'
        in provisioner
    )
    assert (
        'RUNNER_ASSET_SHA256="70920811a4f8ad4328818682bca5c6469c1c942f'
        'ab52448868071d0063816613"'
        in provisioner
    )
    assert 'releases/tags/v${RUNNER_VERSION}' in provisioner
    assert "releases/latest" not in provisioner
    assert '[[ "$asset_url" == "$RUNNER_ASSET_URL" ]]' in provisioner
    assert '[[ "$asset_digest" == "sha256:${RUNNER_ASSET_SHA256}" ]]' in provisioner
    assert "--disableupdate" in provisioner


def test_runner_registration_auth_stays_at_the_root_provisioning_boundary() -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    root_gate = provisioner.index('[[ "$EUID" -eq 0 ]]')
    auth_gate = provisioner.index("gh auth status")
    token_call = provisioner.index("actions/runners/registration-token")
    runner_config = provisioner.index('runuser -u "$RUNNER_USER" -- "$RUNNER_DIR/config.sh"')
    assert root_gate < auth_gate < token_call < runner_config
    assert 'runuser -u "$RUNNER_USER" -- gh' not in provisioner
    assert "/root/.config/gh" not in provisioner
    assert "cp " not in provisioner
    assert 'current_main="$(gh api "repos/${REPOSITORY}/commits/main" --jq .sha)"' in provisioner
    assert '[[ "$current_main" == "$TARGET_SHA" ]]' in provisioner


def test_runner_root_cannot_be_replaced_by_the_unprivileged_runner_user() -> None:
    installer = HOST_INSTALLER.read_text(encoding="utf-8")
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    expected = 'install -d -o root -g "$RUNNER_USER" -m 0750 "$RUNNER_ROOT"'
    assert expected in installer
    assert expected in provisioner


def test_failed_runner_provisioning_removes_the_exact_transient_registration() -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert "cleanup_failed_provisioning" in provisioner
    assert "trap cleanup_failed_provisioning EXIT" in provisioner
    assert 'select(.name == \\"${runner_name}\\") | .id' in provisioner
    assert 'actions/runners/${runner_id}' in provisioner
    assert "trap - EXIT" in provisioner


def test_idle_runner_reaper_never_kills_an_active_deployment() -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert "RuntimeMaxSec" not in provisioner
    assert '--on-active=61m' in provisioner
    assert '--reap) reap_runner_registration' in provisioner
    assert 'if [[ "$runner_status" == "online" && "$runner_busy" == "false" ]]; then' in provisioner
    assert 'systemctl stop "$unit"' in provisioner
    assert 'if [[ "$runner_busy" == "true" ]]; then' in provisioner
    assert 'sleep 5' in provisioner
    assert 'actions/runners/${runner_id}' in provisioner



def _extract_shell_function(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(?P<body>.*?)^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match is not None, name
    return f"{name}() {{\n{match.group('body')}}}\n"


def test_idle_reaper_stops_only_idle_runner_and_deletes_exact_registration(tmp_path) -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    shell = _extract_shell_function(provisioner, "fail") + _extract_shell_function(
        provisioner, "reap_runner_registration"
    )
    shell = shell.replace(
        '[[ "$EUID" -eq 0 ]] || fail "runner reaping must run as root"', ':'
    )
    state = tmp_path / "stopped"
    log = tmp_path / "calls"
    harness = f'''\nREPOSITORY=cavack/WFH-ORG\nSTATE={shlex.quote(str(state))}\nLOG={shlex.quote(str(log))}\ngh() {{\n  if [[ "$1" == auth ]]; then return 0; fi\n  if [[ "$1" == api && "${{2:-}}" == --method ]]; then echo delete >>"$LOG"; return 0; fi\n  if [[ -f "$STATE" ]]; then printf '17\\toffline\\tfalse\\n'; else printf '17\\tonline\\tfalse\\n'; fi\n}}\nsystemctl() {{ [[ "$1" == stop ]] && touch "$STATE" && echo stop >>"$LOG"; return 0; }}\nsleep() {{ :; }}\n{shell}\nreap_runner_registration wfh-production-aaaaaaaaaaaa-123-1\n'''
    result = subprocess.run(["bash", "-c", harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["stop", "delete"]


def test_busy_reaper_never_stops_or_deletes_active_deployment(tmp_path) -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    shell = _extract_shell_function(provisioner, "fail") + _extract_shell_function(
        provisioner, "reap_runner_registration"
    )
    shell = shell.replace(
        '[[ "$EUID" -eq 0 ]] || fail "runner reaping must run as root"', ':'
    )
    log = tmp_path / "calls"
    harness = f'''\nREPOSITORY=cavack/WFH-ORG\nLOG={shlex.quote(str(log))}\ngh() {{\n  if [[ "$1" == auth ]]; then return 0; fi\n  if [[ "$1" == api && "${{2:-}}" == --method ]]; then echo delete >>"$LOG"; return 0; fi\n  printf '17\\tonline\\ttrue\\n'\n}}\nsystemctl() {{ echo stop >>"$LOG"; return 0; }}\nsleep() {{ :; }}\n{shell}\nreap_runner_registration wfh-production-aaaaaaaaaaaa-123-1\n'''
    result = subprocess.run(["bash", "-c", harness], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "remained active past the bounded reap observation window" in result.stderr
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_runner_label_is_bound_to_the_exact_workflow_dispatch_run() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert 'wfh-production-${{ github.run_id }}-${{ github.run_attempt }}' in workflow
    assert 'RUN_ID="${2:-}"' in provisioner
    assert 'LABEL="wfh-production-${RUN_ID}-${run_attempt}"' in provisioner
    assert 'runner_name="wfh-production-${TARGET_SHA:0:12}-${RUN_ID}-${run_attempt}"' in provisioner
    assert 'actions/runs/${RUN_ID}' in provisioner
    assert '[[ "$run_head" == "$TARGET_SHA" ]]' in provisioner
    assert '[[ "$run_event" == "workflow_dispatch" ]]' in provisioner
    assert '[[ "$run_branch" == "main" ]]' in provisioner
    assert '[[ "$run_repository" == "$REPOSITORY" ]]' in provisioner
    assert '.run_attempt' in provisioner
    assert '[[ "$run_attempt" =~ ^[1-9][0-9]*$ ]]' in provisioner


def test_runner_online_guard_covers_all_dispatch_specific_production_labels() -> None:
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert 'startswith(\"wfh-production-\")' in provisioner
    assert '.name == \"wfh-production\"' not in provisioner


def test_runner_provisioning_is_serialized_by_a_root_owned_lock() -> None:
    """Prevent concurrent registrations from sharing names or cleanup state."""
    provisioner = RUNNER_PROVISIONER.read_text(encoding="utf-8")
    assert 'LOCK_FILE="/run/wfh-production-runner.lock"' in provisioner
    assert 'exec 9>"$LOCK_FILE"' in provisioner
    assert 'flock -n 9' in provisioner
    assert provisioner.index('flock -n 9') < provisioner.index("commits/main")


def test_privileged_wrapper_never_executes_runner_writable_source() -> None:
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    assert 'git -C "$DEPLOY_ROOT" fetch --no-tags origin main' in wrapper
    assert '[[ "$(git -C "$DEPLOY_ROOT" rev-parse origin/main)" == "$SHA" ]]' in wrapper
    assert 'git -C "$DEPLOY_ROOT" show "${SHA}:scripts/deploy_production.sh"' in wrapper
    assert 'staged_deploy_script="$(mktemp' in wrapper
    assert 'bash "$staged_deploy_script" </dev/null' in wrapper
    assert '${CHECKOUT}/scripts/deploy_production.sh' not in wrapper
    assert 'bash "$deploy_script"' not in wrapper


def test_privileged_wrapper_binds_manifest_to_all_ci_identities() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    assert '--manifest "$RUNNER_TEMP/wfh-deploy-artifact/image-digests.txt"' in workflow
    assert '--run-id "$GITHUB_RUN_ID"' in workflow
    assert '--run-id) RUN_ID=' in wrapper
    assert '--manifest) MANIFEST=' in wrapper
    assert 'grep -Fxc "backend=$BACKEND_DIGEST" "$MANIFEST"' in wrapper
    assert 'grep -Fxc "frontend=$FRONTEND_DIGEST" "$MANIFEST"' in wrapper
    assert 'grep -Fxc "watchdog=$WATCHDOG_DIGEST" "$MANIFEST"' in wrapper
    assert 'grep -Fxc "revision=$SHA" "$MANIFEST"' in wrapper
    assert 'report.get("tested_backend_image_digest") == expected_backend_digest' not in wrapper
    assert 'type(report.get("valid_until")) is int' in wrapper
    assert '[[ "$(<"$BOUNDARY_REVISION")" == "$SHA" ]]' in wrapper


def test_privileged_wrapper_validates_recovery_contract_and_canonical_hash(tmp_path) -> None:
    """Reject recovery reports that were modified after canonical evaluation."""
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    match = re.search(
        r'/usr/bin/python3 - "\$RECOVERY_REPORT" "\$SHA" <<\'PY\' \|\| exit 1\n'
        r'(?P<script>.*?)\nPY\n',
        wrapper,
        re.DOTALL,
    )
    assert match is not None
    sha = "a" * 40
    body = {
        "contract_version": "release_recovery_gate_report_v1",
        "evaluated_at": int(time.time()),
        "valid_until": int(time.time()) + 3600,
        "source_revision": sha,
        "status": "READY_FOR_EXPLICIT_DISPATCH",
        "blocking_reasons": [],
        "trusted_ci_verification_sha256": "b" * 64,
        "trusted_ci_run_id": 123,
        "tested_backend_image_digest": "sha256:" + "c" * 64,
        "backup_certification_sha256": "d" * 64,
        "migration_rehearsal_sha256": "e" * 64,
        "independent_restore_verification_sha256": "f" * 64,
        "independent_restore_run_id": 456,
        "independent_restore_artifact_id": 789,
        "independent_restore_workflow_revision": sha,
        "deployment_allowed": False,
        "migration_allowed": False,
        "telegram_send_allowed": False,
        "feature_promotion_allowed": False,
        "live_trading_allowed": False,
        "required_next_authority": "EXPLICIT_WORKFLOW_DISPATCH",
    }
    report = {**body, "report_sha256": canonical_sha256(body)}
    report_path = tmp_path / "release-recovery-gate.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    command = [sys.executable, "-", str(report_path), sha]
    valid = subprocess.run(
        command,
        input=match.group("script"),
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr

    report["trusted_ci_run_id"] = 124
    report_path.write_text(json.dumps(report), encoding="utf-8")
    tampered = subprocess.run(
        command,
        input=match.group("script"),
        text=True,
        capture_output=True,
        check=False,
    )
    assert tampered.returncode != 0
    assert "canonical hash mismatch" in tampered.stderr


def test_privileged_wrapper_rebinds_all_artifacts_to_the_current_dispatch_run() -> None:
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    assert 'repos/${REPOSITORY}/actions/runs/${RUN_ID}' in wrapper
    assert 'run_event" == "workflow_dispatch"' in wrapper
    assert 'run_path" == ".github/workflows/ci.yml"' in wrapper
    for job in (
        "backend",
        "frontend",
        "dependency-audit",
        "container-validation",
        "repository-hygiene",
    ):
        assert job in wrapper
    assert 'repos/${REPOSITORY}/actions/jobs/${container_job_id}/logs' in wrapper
    assert "WFH_TESTED_BACKEND_IMAGE_DIGEST" in wrapper
    assert "WFH_TESTED_FRONTEND_IMAGE_DIGEST" in wrapper
    assert "WFH_TESTED_WATCHDOG_IMAGE_DIGEST" in wrapper
    assert "WFH_TESTED_IMAGE_BUNDLE_SHA256" in wrapper


def test_privileged_wrapper_rejects_dirty_or_untrusted_production_checkout() -> None:
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    assert '[[ -z "$(git -C "$DEPLOY_ROOT" status --porcelain' in wrapper
    assert 'require_root_owned_path "$DEPLOY_ROOT"' in wrapper
    assert 'require_root_owned_path "$DEPLOY_ROOT/.git"' in wrapper


def test_runner_owned_artifacts_are_snapshotted_without_privileged_path_reopen() -> None:
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    assert (
        'runuser -u "$RUNNER_USER" -- /bin/cat -- "$MANIFEST" >"$manifest_snapshot"'
        in wrapper
    )
    assert (
        'runuser -u "$RUNNER_USER" -- /bin/cat -- "$BUNDLE" >"$staged_bundle_tmp"'
        in wrapper
    )
    assert 'sha256sum "$BUNDLE"' not in wrapper
    assert 'install -o root -g root -m 0640 "$BUNDLE"' not in wrapper



def test_self_hosted_wrapper_switches_legacy_origin_transactionally() -> None:
    wrapper = PRIVILEGED_WRAPPER.read_text(encoding="utf-8")
    assert 'CANONICAL_ORIGIN="https://github.com/cavack/WFH-ORG.git"' in wrapper
    assert 'LEGACY_ORIGIN="https://github.com/cavack/wfh.git"' in wrapper
    assert 'old_origin="$(git -C "$DEPLOY_ROOT" remote get-url origin)"' in wrapper
    assert 'git -C "$DEPLOY_ROOT" remote set-url origin "$CANONICAL_ORIGIN"' in wrapper
    assert 'git -C "$DEPLOY_ROOT" remote set-url origin "$old_origin"' in wrapper
    assert "restore_origin_on_failure" in wrapper


def test_self_hosted_installer_grants_only_the_root_wrapper() -> None:
    installer = HOST_INSTALLER.read_text(encoding="utf-8")
    assert 'WRAPPER_SOURCE="${SCRIPT_DIR}/wfh-production-deploy"' in installer
    assert 'PROVISIONER_SOURCE="${SCRIPT_DIR}/provision-ephemeral-runner.sh"' in installer
    assert 'install -o root -g root -m 0755 "$WRAPPER_SOURCE" "$WRAPPER_TARGET"' in installer
    assert (
        'install -o root -g root -m 0750 "$PROVISIONER_SOURCE" "$PROVISIONER_TARGET"'
        in installer
    )
    assert '[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$source_main" ]]' in installer
    assert 'wfh-deploy ALL=(root) NOPASSWD: /usr/local/sbin/wfh-production-deploy' in installer
    assert 'wfh-deploy ALL=(ALL) NOPASSWD: ALL' not in installer
    assert 'wfh-deploy ALL=(root) NOPASSWD: ALL' not in installer
    assert 'groupadd --system "$RUNNER_USER"' in installer
    assert 'usermod --gid "$RUNNER_USER" --groups "" --lock' in installer
    assert 'sudo -n -l -U "$RUNNER_USER"' in installer
    assert 'expected_sudo="(root) NOPASSWD: ${WRAPPER_TARGET}"' in installer
    assert "wfh-provision-production-runner" not in installer.split("cat >\"$sudoers_tmp\"", 1)[1]
    assert "runner user must not belong to the docker group" in installer


def test_privileged_deploy_does_not_use_workflow_run_head_code() -> None:
    """Keep privileged deployment on explicit main dispatch, never workflow_run code."""
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    deploy_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    ci_workflow = yaml.load(ci_text, Loader=yaml.BaseLoader)
    assert "workflow_run" not in deploy_text
    assert "github.event.workflow_run" not in deploy_text
    assert ci_workflow["jobs"]["deploy-production"]["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/main' && inputs.deploy_production == true }}"
    )
    deploy_job = deploy_text.split("jobs:\n  deploy:\n", maxsplit=1)[1]
    assert "    permissions:\n      contents: read\n" in deploy_job


def test_deployment_rejects_stale_main_revisions_at_both_boundaries() -> None:
    workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow_equality_gate = 'test "$(git rev-parse origin/main)" = "$WFH_DEPLOY_SHA"'
    script_equality_gate = '[[ "$(git rev-parse origin/main)" == "$WFH_DEPLOY_SHA" ]]'
    assert workflow_equality_gate in workflow_text
    assert script_equality_gate in script_text


def test_production_deploy_workflow_has_no_reusable_remote_credential() -> None:
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "WFH_PROD_SSH_KEY",
        "WFH_DEPLOY_SSH_KEY",
        "WFH_PROD_KNOWN_HOSTS",
        "WFH_DEPLOY_KNOWN_HOSTS",
        "StrictHostKeyChecking",
        "ssh-keyscan",
    ):
        assert forbidden not in text
    assert "wfh-production-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "environment: production" in text


def test_production_deploy_runs_locally_through_narrow_wrapper() -> None:
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    deploy_step = text.split("- name: Deploy exact CI revision on Production host", maxsplit=1)[1]
    assert "sudo /usr/local/sbin/wfh-production-deploy" in deploy_step
    assert "ssh " not in deploy_step
    assert "scp " not in deploy_step
    assert "timeout-minutes: 45" in text


def test_host_deploy_uses_registered_backend_health_paths() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "wait_for_backend_endpoint /livez" in text
    assert "wait_for_backend_endpoint /readyz" in text
    assert "wait_for_backend_endpoint /api/livez" not in text
    assert "wait_for_backend_endpoint /api/readyz" not in text


def test_host_deploy_uses_deploy_owned_lock_and_certified_previous_revision() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'LOCK_FILE="${WFH_DEPLOY_LOCK_FILE:-${STATE_DIR}/deploy.lock}"' in text
    assert "resolve_previous_revision" in text
    assert "restore_previous_workspace" in text
    assert 'PREVIOUS_SHA="$(resolve_previous_revision)"' in text


def test_host_deploy_tracks_possible_migration_mutation_before_apply() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    apply_index = text.index("--apply --source-revision")
    marker_index = text.rfind("MIGRATION_MAY_HAVE_MUTATED=1", 0, apply_index)
    assert marker_index >= 0


def test_host_deploy_failure_and_signal_paths_are_rollback_aware() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    fail_body = text.split("fail() {", maxsplit=1)[1].split("}\n", maxsplit=1)[0]
    assert "terminate_with_cleanup 1" in fail_body
    assert "trap on_error ERR" in text
    assert "trap 'on_signal TERM' TERM" in text
    assert "trap 'on_signal HUP' HUP" in text
    assert "trap 'on_signal INT' INT" in text
    signal_case = text.split('case "$signal" in', maxsplit=1)[1].split("esac", maxsplit=1)[0]
    assert "*) status=1 ;;" in signal_case
    assert "MIGRATION_MAY_HAVE_MUTATED" in text
    assert "RUNTIME_REPLACED" in text


def test_host_deploy_certifies_all_release_containers_healthy() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert text.count("wait_for_container_healthy waterfall-backend") >= 2
    assert text.count("wait_for_container_healthy waterfall-frontend") >= 2
    assert text.count("wait_for_container_healthy waterfall-watchdog") >= 2
    helper = text.split("wait_for_container_healthy() {", maxsplit=1)[1].split(
        "}\n", maxsplit=1
    )[0]
    assert "{{else}}running{{end}}" not in helper
    assert '[[ "$state" == "healthy" ]]' in helper
    assert '|| "$state" == "running"' not in helper
    assert '[[ "$state" == "running" ]]' not in helper
    assert helper.count("return 0") == 1

    monitoring = text.split("wait_for_monitoring_containers_healthy() {", maxsplit=1)[
        1
    ].split("}\n", maxsplit=1)[0]
    assert "wait_for_container_healthy waterfall-prometheus" in monitoring
    assert "wait_for_container_healthy waterfall-grafana" in monitoring
    assert "docker compose ps -q alertmanager" in monitoring
    assert 'wait_for_container_healthy "$alertmanager_container"' in monitoring
    assert text.count("wait_for_monitoring_containers_healthy") >= 3


def test_host_deploy_keeps_telegram_delivery_fail_closed() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assertion = text.split("assert_telegram_delivery_disabled() {", maxsplit=1)[1].split(
        "}\n", maxsplit=1
    )[0]
    assert "docker compose run --rm --no-deps --interactive=false -T waterfall-backend" in assertion
    assert "settings.telegram_signal_delivery_enabled is False" in assertion
    assert "set_env_value TELEGRAM_SIGNAL_DELIVERY_ENABLED true" not in text
    assert "activate_telegram_for_release" not in text
    assert "telegram_signal_delivery_enabled=false" in text


def test_successful_deploy_prunes_backups_before_publishing_certificate() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_sequence = _main_deploy_sequence(text)
    prune_index = main_sequence.index("prune_database_backups")
    certificate_index = main_sequence.index('cat > "${STATE_DIR}/last-successful-deploy.txt"')
    assert prune_index < certificate_index


def test_failed_deploy_retention_preserves_current_certified_backup(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    prune_body = text.split("prune_database_backups() {", maxsplit=1)[1].split(
        "}\n\nassert_telegram_delivery_disabled()", maxsplit=1
    )[0]
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    backup_dir.mkdir()
    state_dir.mkdir()
    backups = [
        backup_dir / f"waterfall_registry.{index}.db" for index in range(4)
    ]
    for index, backup in enumerate(backups):
        backup.write_bytes(f"backup-{index}".encode())
        os.utime(backup, (100 + index, 100 + index))
        backup.with_suffix(backup.suffix + ".sha256").write_text("evidence\n")
    certified_alias = backup_dir / "." / backups[0].name
    (state_dir / "last-successful-deploy.txt").write_text(
        f"revision={'a' * 40}\nbackup={backup_dir}/./{backups[0].name}\n", encoding="utf-8"
    )
    assert certified_alias.resolve() == backups[0].resolve()
    script = f"""set -euo pipefail
BACKUP_DIR={shlex.quote(str(backup_dir))}
STATE_DIR={shlex.quote(str(state_dir))}
WFH_DEPLOY_BACKUP_RETENTION_COUNT=2
prune_database_backups() {{{prune_body}
}}
prune_database_backups
"""
    subprocess.run(["bash", "-c", script], check=True)
    assert backups[0].exists(), "certificate-bound backup must survive failed-deploy pruning"
    assert backups[0].with_suffix(backups[0].suffix + ".sha256").exists()
    assert not backups[1].exists()
    assert not backups[1].with_suffix(backups[1].suffix + ".sha256").exists()
    assert backups[2].exists()
    assert backups[3].exists()


def test_backup_retention_rejects_certificate_path_traversal(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    prune_body = text.split("prune_database_backups() {", maxsplit=1)[1].split(
        "}\n\nassert_telegram_delivery_disabled()", maxsplit=1
    )[0]
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    backup_dir.mkdir()
    state_dir.mkdir()
    backups = [backup_dir / f"waterfall_registry.{index}.db" for index in range(3)]
    for index, backup in enumerate(backups):
        backup.write_bytes(f"backup-{index}".encode())
        os.utime(backup, (100 + index, 100 + index))
    (state_dir / "last-successful-deploy.txt").write_text(
        f"revision={'b' * 40}\nbackup={backup_dir}/../escape.db\n", encoding="utf-8"
    )
    script = f"""set -euo pipefail
BACKUP_DIR={shlex.quote(str(backup_dir))}
STATE_DIR={shlex.quote(str(state_dir))}
WFH_DEPLOY_BACKUP_RETENTION_COUNT=1
prune_database_backups() {{{prune_body}
}}
prune_database_backups
"""
    result = subprocess.run(["bash", "-c", script], check=False)
    assert result.returncode != 0
    assert all(backup.exists() for backup in backups)


def test_compose_run_commands_cannot_consume_streamed_deploy_script() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    normalized = text.replace("\\\n", " ")
    commands = [
        match.group(0)
        for match in re.finditer(r"docker compose run[^\n]+", normalized)
    ]
    assert len(commands) >= 4
    for command in commands:
        assert "--interactive=false" in command
        assert "-T" in command or "--no-TTY" in command


def test_host_deploy_rejects_any_dirty_source_worktree_before_checkout_and_build() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper = text.split("assert_clean_deploy_worktree() {", maxsplit=1)[1].split(
        "}\n", maxsplit=1
    )[0]
    assert "git status --porcelain" in helper
    assert ":(exclude).env" not in helper
    assert ":(exclude).deploy" not in helper
    main_sequence = _main_deploy_sequence(text)
    assert main_sequence.index("assert_clean_deploy_worktree") < main_sequence.index(
        'git checkout --detach "$WFH_DEPLOY_SHA"'
    )


def test_incompatible_post_migration_runtime_is_quarantined() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    rollback = text.split("rollback_previous_revision() {", maxsplit=1)[1].split(
        "terminate_with_cleanup() {", maxsplit=1
    )[0]
    incompatible = rollback.split(
        "if ! previous_revision_accepts_current_schema; then", maxsplit=1
    )[1].split("\n    fi", maxsplit=1)[0]
    assert "docker compose stop" in incompatible
    for service in ("waterfall-backend", "frontend", "watchdog"):
        assert service in incompatible


def test_host_deploy_orders_backup_migration_runtime_and_host_certification() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_sequence = _main_deploy_sequence(text)
    ordered_markers = [
        "flock -n 9",
        '[[ "$(git rev-parse origin/main)" == "$WFH_DEPLOY_SHA" ]]',
        "assert_signal_only_runtime_boundary",
        "load_tested_release_artifacts",
        "assert_telegram_delivery_disabled",
        "backup_database",
        "--preflight",
        "MIGRATION_MAY_HAVE_MUTATED=1",
        "--apply --source-revision",
        "docker compose up -d",
        "/livez",
        "/readyz",
        "wait_for_container_healthy waterfall-frontend",
        "wait_for_container_healthy waterfall-watchdog",
        "org.opencontainers.image.revision",
        "snapshot_host_integration_state",
        "install_systemd_units",
        "install_nginx_site",
        "verify_public_edge",
        "prune_database_backups",
        'cat > "${STATE_DIR}/last-successful-deploy.txt"',
    ]
    positions = [main_sequence.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)


def test_host_deploy_never_enables_live_trading_or_destroys_persistent_volumes() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "LIVE_TRADING_ENABLED=true" not in text
    assert "docker compose down -v" not in text
    assert "StrictHostKeyChecking=no" not in text


def test_host_deploy_auto_uses_host_owned_compose_topology_override() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'PRODUCTION_COMPOSE_OVERRIDE="${STATE_DIR}/production-volumes.override.yml"' in text
    assert "configure_production_compose_topology" in text
    helper = text.split("configure_production_compose_topology() {", maxsplit=1)[1].split(
        "}\n", maxsplit=1
    )[0]
    assert '[[ -f "$PRODUCTION_COMPOSE_OVERRIDE" ]]' in helper
    assert 'export COMPOSE_FILE="${WFH_DEPLOY_ROOT}/docker-compose.yml:${PRODUCTION_COMPOSE_OVERRIDE}"' in helper
    main_sequence = _main_deploy_sequence(text)
    assert main_sequence.index("configure_production_compose_topology") < main_sequence.index(
        "docker compose config --quiet"
    )


def test_host_deploy_uses_one_canonical_compose_project_with_host_override() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper = text.split("configure_production_compose_topology() {", maxsplit=1)[1].split(
        "}\n", maxsplit=1
    )[0]
    assert 'COMPOSE_PROJECT_NAME="waterfallhunter"' in helper
    assert '${COMPOSE_PROJECT_NAME:-waterfallhunter}' not in text
    assert "com.docker.compose.project" not in helper
    assert "waterfall-backend" not in helper


def test_host_deploy_removes_release_containers_before_activation_and_rollback() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper_name = "remove_release_containers_before_compose_handoff"
    assert f"{helper_name}()" in text
    helper = text.split(f"{helper_name}() {{", maxsplit=1)[1].split(
        "}\n", maxsplit=1
    )[0]
    for container in (
        "waterfall-backend",
        "waterfall-frontend",
        "waterfall-watchdog",
        "waterfall-prometheus",
        "waterfall-grafana",
    ):
        assert container in helper
    assert "docker rm -f" in helper
    assert "docker compose config --format json" in helper
    assert 'get("volumes", {}).get("alertmanager_data", {}).get("name", "")' in helper
    assert '--filter "volume=${alertmanager_volume}"' in helper
    assert '[[ "$service" == "alertmanager" ]]' in helper

    rollback = text.split("rollback_previous_revision() {", maxsplit=1)[1].split(
        "terminate_with_cleanup() {", maxsplit=1
    )[0]
    rollback_up = rollback.index("docker compose up -d")
    assert rollback.rfind(helper_name, 0, rollback_up) >= 0

    main_sequence = _main_deploy_sequence(text)
    target_up = main_sequence.index("docker compose up -d")
    assert main_sequence.rfind(helper_name, 0, target_up) >= 0


def test_failed_deploys_also_enforce_backup_retention() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    cleanup = text.split("terminate_with_cleanup() {", maxsplit=1)[1].split(
        "on_error() {", maxsplit=1
    )[0]
    assert "prune_database_backups" in cleanup


def test_default_database_backup_retention_is_two() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'WFH_DEPLOY_BACKUP_RETENTION_COUNT="${WFH_DEPLOY_BACKUP_RETENTION_COUNT:-2}"' in text


def test_host_deploy_uses_canonical_host_owned_env_state_and_backup_paths() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'WFH_HOST_ROOT="${WFH_HOST_ROOT:-/srv/waterfallhunter}"' in text
    assert 'ENV_FILE="${WFH_ENV_FILE:-/etc/waterfallhunter/waterfallhunter.env}"' in text
    assert 'DEPLOY_STATE_DIR="${WFH_HOST_ROOT}/runtime"' in text
    assert 'BACKUP_DIR="${WFH_HOST_ROOT}/backups"' in text
    assert 'PRODUCTION_COMPOSE_OVERRIDE="${STATE_DIR}/production-volumes.override.yml"' in text
    assert 'export WFH_ENV_FILE="$ENV_FILE"' in text


def test_database_backup_runs_as_service_owner_and_promotes_only_after_certification() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    backup = text.split("backup_database() {", maxsplit=1)[1].split(
        "restore_database_backup() {", maxsplit=1
    )[0]
    assert "os.getuid(), os.getgid()" in backup
    assert 'install -d -m 0700 -o "$backend_uid" -g "$backend_gid" "$staging_dir"' in backup
    assert '--user 0:0' not in backup
    assert '-v "${staging_dir}:/backup"' in backup
    assert 'mv -- "${staging_dir}/${backup_name}" "$DB_BACKUP"' in backup
    assert 'sha256sum "${staging_dir}/${backup_name}"' in backup
    assert '"$actual_staged_sha" != "$DB_BACKUP_SHA256"' in backup
    assert backup.index("PRAGMA integrity_check") < backup.index(
        'mv -- "${staging_dir}/${backup_name}" "$DB_BACKUP"'
    )
    assert backup.index('"$actual_staged_sha" != "$DB_BACKUP_SHA256"') < backup.index(
        'mv -- "${staging_dir}/${backup_name}" "$DB_BACKUP"'
    )
    assert "staging_created=0" in backup
    assert backup.count(
        'rm -f -- "${staging_dir}/${backup_name}" "$DB_BACKUP" "${DB_BACKUP}.sha256"'
    ) >= 3


def test_release_image_override_scopes_restrictive_umask() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper = text.split("write_release_image_override() {", maxsplit=1)[1].split(
        "verify_image_revision() {", maxsplit=1
    )[0]
    assert "umask 027" in helper
    assert "(\n    umask 027" in helper


def test_monitoring_bind_permissions_are_checked_before_database_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "assert_monitoring_bind_files_readable()" in text
    helper = text.split("assert_monitoring_bind_files_readable() {", maxsplit=1)[1].split(
        "configure_production_compose_topology() {", maxsplit=1
    )[0]
    assert "--entrypoint /bin/promtool prometheus" in helper
    assert "check config /etc/prometheus/prometheus.yml" in helper
    main_sequence = _main_deploy_sequence(text)
    permission_index = main_sequence.index("assert_monitoring_bind_files_readable")
    backup_index = main_sequence.index("backup_database")
    migration_index = main_sequence.index("migrate_database")
    assert permission_index < backup_index < migration_index


def test_schema_rollback_restore_runs_as_backend_service_owner() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    restore = text.split("restore_database_backup() {", maxsplit=1)[1].split(
        "prune_database_backups() {", maxsplit=1
    )[0]
    assert "os.getuid(), os.getgid()" in restore
    assert '--user 0:0' not in restore
    assert '--user "$backend_uid:$backend_gid"' in restore
    assert "backup_gid=\"$(stat -c '%g' \"$DB_BACKUP\")\"" in restore
    assert "backup_mode=\"$(stat -c '%a' \"$DB_BACKUP\")\"" in restore
    assert 'chgrp "$backend_gid" "$DB_BACKUP"' in restore
    assert '-v "${DB_BACKUP}:/backup/${backup_name}:ro"' in restore
    assert "trap restore_backup_metadata EXIT" in restore
    assert "trap 'exit 129' HUP" in restore
    assert "trap 'exit 130' INT" in restore
    assert "trap 'exit 143' TERM" in restore
    assert 'chgrp "$backup_gid" "$DB_BACKUP"' in restore
    assert 'chmod "$backup_mode" "$DB_BACKUP"' in restore
    assert "source.backup(target)" in restore
    stop_line = "docker compose stop waterfall-backend frontend watchdog"
    assert stop_line in restore
    stop_stmt = next(line.strip() for line in restore.splitlines() if stop_line in line)
    assert "|| true" not in stop_stmt
    assert "|| exit 1" in stop_stmt
    assert restore.index(stop_line) < restore.index("source.backup(target)")
    assert "shutil.copyfile" not in restore
    assert ".rollback-" not in restore


def test_deploy_clean_worktree_no_longer_depends_on_runtime_files_inside_git() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper = text.split("assert_clean_deploy_worktree() {", maxsplit=1)[1].split("}\n", maxsplit=1)[0]
    assert "git status --porcelain" in helper
    assert ":(exclude).env" not in helper
    assert ":(exclude).deploy" not in helper


def test_schema_changing_rollback_restores_backup_before_previous_schema_preflight() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "restore_database_backup()" in text
    rollback = text.split("rollback_previous_revision() {", maxsplit=1)[1].split(
        "terminate_with_cleanup() {", maxsplit=1
    )[0]
    restore_index = rollback.index("restore_database_backup")
    preflight_index = rollback.index("previous_revision_accepts_current_schema")
    assert restore_index < preflight_index
    restore = text.split("restore_database_backup() {", maxsplit=1)[1].split(
        "prune_database_backups() {", maxsplit=1
    )[0]
    assert 'sha256sum "$DB_BACKUP"' in restore
    assert '${BACKUP_DIR}:/backup:ro' not in restore
    assert '${DB_BACKUP}:/backup/${backup_name}:ro' in restore
    assert 'source.backup(target)' in restore
    assert 'file:{src}?mode=ro&immutable=1' in restore
    assert "docker compose stop waterfall-backend frontend watchdog" in restore
    assert "PRAGMA integrity_check" in restore


def test_deploy_installs_and_enables_canonical_systemd_units_after_runtime_health() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "install_systemd_units()" in text
    helper = text.split("install_systemd_units() {", maxsplit=1)[1].split("}\n", maxsplit=1)[0]
    for unit in (
        "waterfallhunter.service",
        "waterfallhunter-healthcheck.service",
        "waterfallhunter-healthcheck.timer",
    ):
        assert unit in helper
    assert "systemctl daemon-reload" in helper
    assert "systemctl enable waterfallhunter.service waterfallhunter-healthcheck.timer" in helper
    assert "host integration snapshot missing before systemd install" in helper
    main_sequence = _main_deploy_sequence(text)
    runtime_index = main_sequence.index("docker compose up -d")
    healthy_index = main_sequence.index("verify_running_signal_only")
    snapshot_index = main_sequence.index("snapshot_host_integration_state")
    install_index = main_sequence.index("install_systemd_units")
    certificate_index = main_sequence.index('cat > "${STATE_DIR}/last-successful-deploy.txt"')
    assert runtime_index < healthy_index < snapshot_index < install_index < certificate_index


def test_main_deploy_loads_ci_tested_images_instead_of_rebuilding_target() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_sequence = _main_deploy_sequence(text)
    assert "load_tested_release_artifacts" in main_sequence
    load_index = main_sequence.index("load_tested_release_artifacts")
    backup_index = main_sequence.index("backup_database")
    assert load_index < backup_index
    target_prefix = main_sequence[:backup_index]
    assert "docker compose build" not in target_prefix
    helper = text.split("load_tested_release_artifacts() {", maxsplit=1)[1].split("}\n", maxsplit=1)[0]
    assert 'docker load -i "$WFH_TESTED_IMAGE_BUNDLE"' in helper
    assert "WFH_TESTED_BACKEND_IMAGE_DIGEST" in helper
    assert "WFH_TESTED_FRONTEND_IMAGE_DIGEST" in helper
    assert "WFH_TESTED_WATCHDOG_IMAGE_DIGEST" in helper
    assert "WFH_TESTED_IMAGE_BUNDLE_SHA256" in helper


def test_streamed_remote_deploy_does_not_let_compose_consume_script_tail() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_sequence = _main_deploy_sequence(text)
    assert "docker compose up -d --remove-orphans --no-build </dev/null" in main_sequence
    compose_up_lines = [
        line.strip() for line in text.splitlines() if line.strip().startswith("docker compose up ")
    ]
    assert compose_up_lines
    assert all("</dev/null" in line for line in compose_up_lines)


def test_self_hosted_deploy_uses_runner_temp_artifact_and_root_wrapper() -> None:
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert "path: ${{ runner.temp }}/wfh-deploy-artifact" in text
    deploy_step = text.split("      - name: Deploy exact CI revision on Production host\n", maxsplit=1)[1]
    assert "sudo /usr/local/sbin/wfh-production-deploy" in deploy_step
    assert '--checkout "$GITHUB_WORKSPACE"' in deploy_step
    assert '--bundle "$RUNNER_TEMP/wfh-deploy-artifact/wfh-tested-images.tar"' in deploy_step
    assert "bash -s" not in deploy_step
    assert "ssh " not in deploy_step
    assert "scp " not in deploy_step


def test_public_edge_gate_targets_the_dashboard_base_path() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert (
        'local public_url="${WFH_PUBLIC_EDGE_URL:-http://waterfall.booksreadlive.online/dashboard/}"'
        in text
    )



def test_production_reusable_workflow_requires_no_deploy_secrets() -> None:
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    deploy_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    deploy_job = ci_text.split("\n  deploy-production:\n", maxsplit=1)[1]
    assert "secrets: inherit" not in deploy_job
    assert "\n    secrets:\n" not in deploy_job
    assert "\n    secrets:\n" not in deploy_text
    for secret in (
        "WFH_PROD_HOST",
        "WFH_DEPLOY_HOST",
        "WFH_PROD_SSH_KEY",
        "WFH_DEPLOY_SSH_KEY",
        "WFH_PROD_KNOWN_HOSTS",
        "WFH_DEPLOY_KNOWN_HOSTS",
    ):
        assert secret not in deploy_job
        assert secret not in deploy_text


def test_deploy_verifies_portable_bundle_config_digest_not_daemon_local_image_id() -> None:
    """Require portable bundle verification instead of daemon-local Docker image IDs."""
    helper = (ROOT / "scripts/deploy_production.sh").read_text(encoding="utf-8")
    assert "verify_ci_image_bundle.py" in helper
    assert '--allowed-root "${STATE_DIR}/incoming/${WFH_DEPLOY_SHA}"' in helper
    assert 'docker image inspect "$image_name" --format \'{{.Id}}\'' not in helper


def test_ci_exports_and_uploads_exact_tested_image_bundle_to_deploy_job() -> None:
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    deploy_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    container_job = ci_text.split("\n  container-validation:\n", maxsplit=1)[1].split(
        "\n  repository-hygiene:\n", maxsplit=1
    )[0]
    assert "outputs:" in container_job
    assert "tested_backend_image_digest" in container_job
    assert "tested_frontend_image_digest" in container_job
    assert "tested_watchdog_image_digest" in container_job
    assert "tested_image_bundle_sha256" in container_job
    assert "docker save" in container_job
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in container_job

    deploy_job = ci_text.split("\n  deploy-production:\n", maxsplit=1)[1]
    assert "tested_backend_image_digest: ${{ needs.container-validation.outputs.tested_backend_image_digest }}" in deploy_job
    assert "tested_frontend_image_digest: ${{ needs.container-validation.outputs.tested_frontend_image_digest }}" in deploy_job
    assert "tested_watchdog_image_digest: ${{ needs.container-validation.outputs.tested_watchdog_image_digest }}" in deploy_job
    assert "tested_image_bundle_sha256: ${{ needs.container-validation.outputs.tested_image_bundle_sha256 }}" in deploy_job

    assert "workflow_call:" in deploy_text
    assert "tested_backend_image_digest:" in deploy_text
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in deploy_text
    assert "WFH_TESTED_IMAGE_BUNDLE_SHA256" in deploy_text
    assert "WFH_TESTED_BACKEND_IMAGE_DIGEST" in deploy_text


def test_production_compose_wrapper_exports_canonical_env_path() -> None:
    wrapper = (ROOT / "scripts/production_compose.sh").read_text(encoding="utf-8")
    assert 'ENV_FILE="${WFH_ENV_FILE:-/etc/waterfallhunter/waterfallhunter.env}"' in wrapper
    assert 'export WFH_ENV_FILE="$ENV_FILE"' in wrapper


def test_ci_tested_image_bundle_upload_path_is_not_hidden() -> None:
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    container_job = ci_text.split("\n  container-validation:\n", maxsplit=1)[1].split(
        "\n  repository-hygiene:\n", maxsplit=1
    )[0]
    assert "path: ci-artifacts/" in container_job
    assert ".ci-artifacts/" not in container_job


def test_deploy_pins_target_images_to_release_specific_tags_before_compose_activation() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'RELEASE_BACKEND_IMAGE="wfh-release-backend:${WFH_DEPLOY_SHA}"' in text
    assert 'RELEASE_FRONTEND_IMAGE="wfh-release-frontend:${WFH_DEPLOY_SHA}"' in text
    assert 'RELEASE_WATCHDOG_IMAGE="wfh-release-watchdog:${WFH_DEPLOY_SHA}"' in text
    helper = text.split("pin_target_release_images() {", maxsplit=1)[1].split("}\n", maxsplit=1)[0]
    assert 'docker tag waterfallhunter-waterfall-backend "$RELEASE_BACKEND_IMAGE"' in helper
    assert 'docker tag waterfallhunter-frontend "$RELEASE_FRONTEND_IMAGE"' in helper
    assert 'docker tag waterfallhunter-watchdog "$RELEASE_WATCHDOG_IMAGE"' in helper
    assert "write_release_image_override" in helper
    main_sequence = _main_deploy_sequence(text)
    assert main_sequence.index("pin_target_release_images") < main_sequence.index("backup_database")
    assert main_sequence.index("activate_target_image_override") < main_sequence.index("backup_database")


def test_deploy_rollback_pins_previous_running_images_before_loading_target_bundle() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_sequence = _main_deploy_sequence(text)
    assert "pin_previous_running_images" in main_sequence
    assert main_sequence.index("pin_previous_running_images") < main_sequence.index("load_tested_release_artifacts")
    rollback = text.split("rollback_previous_revision() {", maxsplit=1)[1].split("terminate_with_cleanup() {", maxsplit=1)[0]
    assert "activate_rollback_image_override" in rollback
    assert rollback.index("activate_rollback_image_override") < rollback.index("docker compose up -d")


def test_successful_deploy_promotes_immutable_image_override_for_systemd_restarts() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_sequence = _main_deploy_sequence(text)
    assert "promote_target_image_override" in main_sequence
    assert main_sequence.index("verify_running_revision") < main_sequence.index("promote_target_image_override")
    assert main_sequence.index("promote_target_image_override") < main_sequence.index("install_systemd_units")
    wrapper = (ROOT / "scripts/production_compose.sh").read_text(encoding="utf-8")
    assert 'IMAGE_OVERRIDE="${WFH_PRODUCTION_IMAGE_OVERRIDE:-/srv/waterfallhunter/runtime/production-images.override.yml}"' in wrapper
    assert 'if [[ -f "$IMAGE_OVERRIDE" ]]; then' in wrapper
    assert 'compose_args+=(-f "$IMAGE_OVERRIDE")' in wrapper
