"""Unit tests for aks_check_deprecated_apis (single-batched-Run-Command implementation).

Uses a small synthetic KNOWN_API_DEPRECATIONS list (monkeypatched) so tests are independent
of the real, evolving matrix content, and a mocked run_kubectl_raw for deterministic behavior.
No live AKS cluster or Azure credentials are used.
"""

from __future__ import annotations

import pytest

from tools import deprecated_apis
from tools.deprecated_apis import (
    _build_batch_script,
    _parse_batch_output,
    _validate_namespace,
    aks_check_deprecated_apis,
    classify_entry,
    determine_deprecated_api_health,
)

_SYNTHETIC_MATRIX = [
    {
        "group": "policy",
        "version": "v1beta1",
        "kind": "PodDisruptionBudget",
        "plural": "poddisruptionbudgets",
        "namespaced": True,
        "deprecated_in": (1, 21),
        "removed_in": (1, 25),
        "replacement": "policy/v1 PodDisruptionBudget",
    },
    {
        "group": "extensions",
        "version": "v1beta1",
        "kind": "Ingress",
        "plural": "ingresses",
        "namespaced": True,
        "deprecated_in": (1, 14),
        "removed_in": (1, 22),
        "replacement": "networking.k8s.io/v1 Ingress",
    },
]


def _patch_matrix(monkeypatch):
    monkeypatch.setattr(deprecated_apis, "KNOWN_API_DEPRECATIONS", _SYNTHETIC_MATRIX)


def _batch_output(*entries: tuple[int, int, int]) -> str:
    """Build the compact batched output format: (index, exit_code, count) tuples."""
    lines = []
    for index, exit_code, count in entries:
        lines.append(f"===ENTRY:{index}===")
        lines.append(f"EXIT={exit_code} COUNT={count}")
    return "\n".join(lines)


def test_no_deprecated_apis_detected(monkeypatch):
    """Test 1: every relevant entry is available with zero objects -> HEALTHY, no findings/errors."""
    _patch_matrix(monkeypatch)
    monkeypatch.setattr(
        deprecated_apis, "run_kubectl_raw", lambda *a, **k: _batch_output((0, 0, 0), (1, 0, 0))
    )

    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.26.0")

    assert result["deprecated_api_health"] == "HEALTHY"
    assert result["findings"] == []
    assert result["blockers"] == []
    assert result["warnings"] == []
    assert result["query_errors"] == []
    assert result["run_command_invocations"] == 1


def test_deprecated_api_still_served_is_a_warning(monkeypatch):
    """Test 2: target version is past deprecated_in but before removed_in -> WARNING, not blocked."""
    _patch_matrix(monkeypatch)
    # PDB v1beta1 is entry 0; deprecated at 1.21, removed at 1.25. Target 1.23 => deprecated, still served.
    monkeypatch.setattr(deprecated_apis, "run_kubectl_raw", lambda *a, **k: _batch_output((0, 0, 3), (1, 0, 0)))

    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.23.0")

    assert result["deprecated_api_health"] == "WARNING"
    assert result["blockers"] == []
    assert len(result["warnings"]) == 1
    finding = result["findings"][0]
    assert finding["status"] == "DEPRECATED_STILL_SERVED"
    assert finding["severity"] == "WARNING"
    assert finding["kind"] == "PodDisruptionBudget"
    assert finding["count"] == 3


def test_removed_api_in_target_is_a_blocker(monkeypatch):
    """Test 3: target version is at/after removed_in -> BLOCKER."""
    _patch_matrix(monkeypatch)
    monkeypatch.setattr(deprecated_apis, "run_kubectl_raw", lambda *a, **k: _batch_output((0, 0, 1), (1, 0, 0)))

    # PDB v1beta1 removed at 1.25. Target 1.25 => removed.
    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.25.0")

    assert result["deprecated_api_health"] == "BLOCKED"
    assert len(result["blockers"]) == 1
    finding = result["findings"][0]
    assert finding["status"] == "REMOVED_IN_TARGET"
    assert finding["severity"] == "BLOCKER"
    assert finding["count"] == 1


def test_multiple_findings_across_api_versions(monkeypatch):
    """Test 4: multiple deprecated/removed APIs in use are all reported from one batched call."""
    _patch_matrix(monkeypatch)
    monkeypatch.setattr(deprecated_apis, "run_kubectl_raw", lambda *a, **k: _batch_output((0, 0, 2), (1, 0, 5)))

    # Target 1.25: PDB v1beta1 removed at 1.25 (BLOCKER); Ingress extensions/v1beta1 removed at 1.22 (also BLOCKER).
    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.25.0")

    assert result["run_command_invocations"] == 1
    assert len(result["findings"]) == 2
    kinds = {f["kind"] for f in result["findings"]}
    assert kinds == {"PodDisruptionBudget", "Ingress"}
    assert result["deprecated_api_health"] == "BLOCKED"


def test_api_unavailable_is_distinguished_from_no_objects_found(monkeypatch):
    """Test 5: a non-zero kubectl exit (API unavailable/removed) must NOT be read as 'no objects
    found' - it should be surfaced as a query error, not silently treated as evidence of health."""
    _patch_matrix(monkeypatch)
    # Entry 0 (PDB) is unavailable on this cluster (non-zero exit); entry 1 (Ingress) is available, empty.
    monkeypatch.setattr(deprecated_apis, "run_kubectl_raw", lambda *a, **k: _batch_output((0, 1, 0), (1, 0, 0)))

    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.25.0")

    assert result["findings"] == []
    assert len(result["query_errors"]) == 1
    assert "not available" in result["query_errors"][0]
    assert "PodDisruptionBudget" in result["query_errors"][0]


def test_missing_target_version_falls_back_to_cluster_current_version(monkeypatch):
    """Test 6a: no target_version supplied -> falls back to cluster's current version, no error."""
    _patch_matrix(monkeypatch)
    monkeypatch.setattr(deprecated_apis, "run_kubectl_raw", lambda *a, **k: _batch_output((0, 0, 0), (1, 0, 0)))
    monkeypatch.setattr(
        deprecated_apis, "aks_get_cluster_details", lambda *a, **k: {"kubernetes_version": "1.27.7"}
    )

    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version=None)

    assert result["target_kubernetes_version"] == "1.27.7"
    assert result["target_version_source"] == "cluster_current_version (no target_version provided)"


def test_invalid_target_version_raises(monkeypatch):
    """Test 6b: malformed target_version raises ValueError rather than guessing."""
    _patch_matrix(monkeypatch)

    with pytest.raises(ValueError):
        aks_check_deprecated_apis("sub", "rg", "cluster", target_version="not-a-version")


def test_classify_entry_returns_none_when_not_yet_relevant():
    entry = _SYNTHETIC_MATRIX[0]  # PDB: deprecated_in (1, 21)
    assert classify_entry(entry, (1, 18), "1.18.0") is None


def test_determine_deprecated_api_health():
    assert determine_deprecated_api_health([], []) == "HEALTHY"
    assert determine_deprecated_api_health([], ["w"]) == "WARNING"
    assert determine_deprecated_api_health(["b"], ["w"]) == "BLOCKED"


def test_parse_batch_output_extracts_multiple_entries():
    """Test 7: the compact batched-response format is parsed into {index: (exit_code, count)}."""
    raw = _batch_output((0, 0, 0), (1, 1, 0), (2, 0, 4))

    parsed = _parse_batch_output(raw)

    assert parsed == {0: (0, 0), 1: (1, 0), 2: (0, 4)}


def test_parse_batch_output_ignores_surrounding_noise():
    """Test 7b: extra banner/log text around the markers doesn't break parsing."""
    raw = "some run-command banner text\n" + _batch_output((0, 0, 2)) + "\ncommand completed successfully\n"

    parsed = _parse_batch_output(raw)

    assert parsed == {0: (0, 2)}


def test_build_batch_script_uses_single_invocation_shape():
    """A batch script for N entries contains N ENTRY markers and requests only compact `-o name`
    output (never full JSON) - the caller issues the whole script as ONE run_kubectl_raw call."""
    script = _build_batch_script(_SYNTHETIC_MATRIX, ns_flag="-A")

    assert script.count("===ENTRY:0===") == 1
    assert script.count("===ENTRY:1===") == 1
    assert "-o name" in script
    assert "-o json" not in script


def test_invalid_namespace_raises():
    with pytest.raises(ValueError):
        _validate_namespace("not; a valid namespace")
