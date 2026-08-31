"""Unit tests for aks_check_deprecated_apis.

Uses a small synthetic KNOWN_API_DEPRECATIONS list (monkeypatched) so tests are independent
of the real, evolving matrix content, and mocked kubectl responses for deterministic behavior.
No live AKS cluster or Azure credentials are used.
"""

from __future__ import annotations

import pytest

from tools import deprecated_apis
from tools.deprecated_apis import aks_check_deprecated_apis, classify_entry, determine_deprecated_api_health

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


def _item(namespace: str | None, name: str) -> dict:
    return {"metadata": {"namespace": namespace, "name": name}}


def _patch_matrix(monkeypatch):
    monkeypatch.setattr(deprecated_apis, "KNOWN_API_DEPRECATIONS", _SYNTHETIC_MATRIX)


def test_no_deprecated_apis_detected(monkeypatch):
    """Test 1: target version relevant, but kubectl returns no matching objects -> HEALTHY."""
    _patch_matrix(monkeypatch)
    monkeypatch.setattr(deprecated_apis, "run_kubectl_json", lambda *a, **k: {"items": []})

    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.26.0")

    assert result["deprecated_api_health"] == "HEALTHY"
    assert result["findings"] == []
    assert result["blockers"] == []
    assert result["warnings"] == []


def test_deprecated_api_still_served_is_a_warning(monkeypatch):
    """Test 2: target version is past deprecated_in but before removed_in -> WARNING, not blocked."""
    _patch_matrix(monkeypatch)

    def fake_kubectl(subscription_id, resource_group, cluster_name, command):
        if "poddisruptionbudgets" in command:
            return {"items": [_item("payments", "db-pdb")]}
        return {"items": []}

    monkeypatch.setattr(deprecated_apis, "run_kubectl_json", fake_kubectl)

    # PDB v1beta1: deprecated at 1.21, removed at 1.25. Target 1.23 => still served, deprecated.
    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.23.0")

    assert result["deprecated_api_health"] == "WARNING"
    assert result["blockers"] == []
    assert len(result["warnings"]) == 1
    finding = result["findings"][0]
    assert finding["status"] == "DEPRECATED_STILL_SERVED"
    assert finding["severity"] == "WARNING"
    assert finding["namespace"] == "payments"
    assert finding["name"] == "db-pdb"


def test_removed_api_in_target_is_a_blocker(monkeypatch):
    """Test 3: target version is at/after removed_in -> BLOCKER."""
    _patch_matrix(monkeypatch)

    def fake_kubectl(subscription_id, resource_group, cluster_name, command):
        if "poddisruptionbudgets" in command:
            return {"items": [_item("payments", "db-pdb")]}
        return {"items": []}

    monkeypatch.setattr(deprecated_apis, "run_kubectl_json", fake_kubectl)

    # PDB v1beta1 removed at 1.25. Target 1.25 => removed.
    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.25.0")

    assert result["deprecated_api_health"] == "BLOCKED"
    assert len(result["blockers"]) == 1
    finding = result["findings"][0]
    assert finding["status"] == "REMOVED_IN_TARGET"
    assert finding["severity"] == "BLOCKER"


def test_multiple_findings_across_api_versions(monkeypatch):
    """Test 4: multiple deprecated/removed APIs in use are all reported."""
    _patch_matrix(monkeypatch)

    def fake_kubectl(subscription_id, resource_group, cluster_name, command):
        if "poddisruptionbudgets" in command:
            return {"items": [_item("payments", "db-pdb")]}
        if "ingresses" in command:
            return {"items": [_item("web", "legacy-ingress")]}
        return {"items": []}

    monkeypatch.setattr(deprecated_apis, "run_kubectl_json", fake_kubectl)

    # Target 1.25: PDB v1beta1 removed at 1.25 (BLOCKER); Ingress extensions/v1beta1 removed at 1.22 (also BLOCKER).
    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version="1.25.0")

    assert len(result["findings"]) == 2
    kinds = {f["kind"] for f in result["findings"]}
    assert kinds == {"PodDisruptionBudget", "Ingress"}
    assert result["deprecated_api_health"] == "BLOCKED"


def test_missing_target_version_falls_back_to_cluster_current_version(monkeypatch):
    """Test 5a: no target_version supplied -> falls back to cluster's current version, no error."""
    _patch_matrix(monkeypatch)
    monkeypatch.setattr(deprecated_apis, "run_kubectl_json", lambda *a, **k: {"items": []})
    monkeypatch.setattr(
        deprecated_apis, "aks_get_cluster_details", lambda *a, **k: {"kubernetes_version": "1.27.7"}
    )

    result = aks_check_deprecated_apis("sub", "rg", "cluster", target_version=None)

    assert result["target_kubernetes_version"] == "1.27.7"
    assert result["target_version_source"] == "cluster_current_version (no target_version provided)"


def test_invalid_target_version_raises(monkeypatch):
    """Test 5b: malformed target_version raises ValueError rather than guessing."""
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
