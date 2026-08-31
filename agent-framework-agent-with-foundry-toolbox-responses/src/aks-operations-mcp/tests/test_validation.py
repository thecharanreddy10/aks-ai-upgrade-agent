"""Unit tests for aks_check_pod_health (compact jsonpath, single-Run-Command implementation).

A mocked run_kubectl_raw is used for deterministic behavior - no live AKS cluster or Azure
credentials are required.
"""

from __future__ import annotations

import pytest

from tools import validation
from tools.validation import (
    _build_pod_health_script,
    _parse_pod_health_output,
    _validate_namespace,
    aks_check_pod_health,
)


def _pod_line(
    namespace: str,
    name: str,
    phase: str,
    restarts: str = "",
    waiting: str = "",
    sched_status: str = "",
    sched_reason: str = "",
    ready_status: str = "",
) -> str:
    return "|".join([namespace, name, phase, restarts, waiting, sched_status, sched_reason, ready_status])


def _raw_output(lines: list[str], exit_code: int = 0) -> str:
    body = "\n".join(lines)
    return f"{body}\n===PODS_EXIT={exit_code}===\n"


def test_healthy_pods_report_healthy_status(monkeypatch):
    lines = [
        _pod_line("payments", "api-1", "Running", restarts="0", ready_status="True"),
        _pod_line("payments", "worker-1", "Succeeded", restarts="0"),
    ]
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["total_pods"] == 2
    assert result["healthy_pods"] == 2
    assert result["unhealthy_pods"] == []
    assert result["pod_health_status"] == "HEALTHY"
    assert result["query_errors"] == []
    assert result["run_command_invocations"] == 1
    assert result["cluster_name"] == "cluster"
    assert result["scope"] == "all-namespaces"


def test_pending_pod_is_unhealthy(monkeypatch):
    lines = [
        _pod_line("billing", "invoices-app", "Pending", sched_status="False", sched_reason="Unschedulable"),
    ]
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "UNHEALTHY"
    assert len(result["unhealthy_pods"]) == 1
    pod = result["unhealthy_pods"][0]
    assert pod["namespace"] == "billing"
    assert pod["phase"] == "Pending"
    assert pod["scheduled_reason"] == "Unschedulable"


def test_crashloopbackoff_running_pod_is_unhealthy(monkeypatch):
    """A pod can be phase=Running while one container is CrashLoopBackOff - must still be flagged."""
    lines = [_pod_line("web", "frontend-1", "Running", restarts="4", waiting="CrashLoopBackOff", ready_status="False")]
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "UNHEALTHY"
    pod = result["unhealthy_pods"][0]
    assert pod["restart_count"] == 4
    assert pod["waiting_reasons"] == ["CrashLoopBackOff"]


def test_readiness_failure_without_waiting_reason_is_unhealthy(monkeypatch):
    """A Running pod whose Ready condition is False (failing readiness probe) is unhealthy even
    with no container waiting reason present."""
    lines = [_pod_line("web", "backend-1", "Running", restarts="0", ready_status="False")]
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "UNHEALTHY"
    assert result["unhealthy_pods"][0]["name"] == "backend-1"


def test_multiple_namespaces_reported_correctly(monkeypatch):
    lines = [
        _pod_line("payments", "api-1", "Running", ready_status="True"),
        _pod_line("billing", "worker-1", "Running", ready_status="True"),
        _pod_line("billing", "worker-2", "Pending", waiting="ImagePullBackOff"),
    ]
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["total_pods"] == 3
    namespaces = {pod["namespace"] for pod in result["unhealthy_pods"]}
    assert namespaces == {"billing"}


def test_kubectl_exit_failure_is_incomplete_not_healthy(monkeypatch):
    """A non-zero kubectl exit must never be reported as a healthy/empty result."""
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output([], exit_code=1))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "INCOMPLETE"
    assert result["total_pods"] == 0
    assert result["unhealthy_pods"] == []
    assert any("exited with code 1" in err for err in result["query_errors"])


def test_run_command_exception_is_incomplete_not_healthy(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("AKS run command did not return logs output.")

    monkeypatch.setattr(validation, "run_kubectl_raw", _raise)

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "INCOMPLETE"
    assert result["total_pods"] == 0
    assert any("pod health check failed" in err for err in result["query_errors"])


def test_malformed_row_is_recorded_as_query_error_not_hidden(monkeypatch):
    lines = ["payments|api-1|Running"]  # missing fields
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "INCOMPLETE"
    assert result["total_pods"] == 0
    assert any("Malformed pod health row" in err for err in result["query_errors"])


def test_confirmed_unhealthy_pods_take_precedence_over_incomplete(monkeypatch):
    """A confirmed unhealthy pod alongside a malformed row must still be reported as UNHEALTHY,
    while the malformed row is still visible in query_errors (nothing is hidden)."""
    lines = [
        _pod_line("web", "frontend-1", "Pending"),
        "malformed|row",
    ]
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output(lines))

    result = aks_check_pod_health("sub", "rg", "cluster")

    assert result["pod_health_status"] == "UNHEALTHY"
    assert len(result["unhealthy_pods"]) == 1
    assert any("Malformed pod health row" in err for err in result["query_errors"])


def test_build_pod_health_script_uses_single_invocation_and_never_full_json():
    script = _build_pod_health_script("-A")

    assert "-o jsonpath=" in script
    assert "-o json " not in script
    assert not script.rstrip().endswith("-o json")
    assert script.count("kubectl") == 1


def test_build_pod_health_script_scopes_to_namespace():
    script = _build_pod_health_script("-n payments")

    assert "-n payments" in script
    assert " -A" not in script


def test_parse_pod_health_output_extracts_exit_code_and_rows():
    raw = _raw_output([_pod_line("payments", "api-1", "Running", restarts="2", ready_status="True")], exit_code=0)

    rows, exit_code, parse_errors = _parse_pod_health_output(raw)

    assert exit_code == 0
    assert parse_errors == []
    assert rows == [
        {
            "namespace": "payments",
            "name": "api-1",
            "phase": "Running",
            "restart_count": 2,
            "waiting_reasons": [],
            "scheduled_status": None,
            "scheduled_reason": None,
            "ready_status": "True",
        }
    ]


def test_invalid_namespace_raises():
    with pytest.raises(ValueError):
        _validate_namespace("not; a valid namespace")


def test_aks_check_pod_health_validates_namespace(monkeypatch):
    monkeypatch.setattr(validation, "run_kubectl_raw", lambda *a, **k: _raw_output([]))

    with pytest.raises(ValueError):
        aks_check_pod_health("sub", "rg", "cluster", namespace="bad; namespace")
