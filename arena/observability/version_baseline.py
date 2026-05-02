# arena/observability/version_baseline.py
from __future__ import annotations

import json
from pathlib import Path


def record_provider_version(
    *,
    competition_slug: str,
    provider: str,
    version: str,
    root: str | Path = "runs/.baselines",
) -> tuple[bool, str | None]:
    """Record a provider+version pair against the per-slug baseline.

    The baseline file lives at `runs/.baselines/<competition_slug>/provider_versions.json`.
    Maps `{provider: version}`. SCOPED PER SLUG (not per run_id) so drift
    is detected across `arena init-fixture` cycles — a fresh run that
    introduces a new provider version is correctly flagged.

    First call for a (slug, provider) pair initializes the entry and
    returns (True, None). Subsequent calls with the SAME version return
    (False, None). A call with a DIFFERENT version returns
    (False, "<old_version>") and DOES NOT overwrite — the baseline is
    sticky so drift is consistently flagged across all subsequent
    invocations until a human deliberately resets it.

    Caller is responsible for emitting the corresponding
    `provider_version_recorded` event with the correct severity (info on
    new baseline, warning on drift).
    """
    baseline_path = Path(root) / competition_slug / "provider_versions.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    if baseline_path.exists():
        baseline: dict[str, str] = json.loads(baseline_path.read_text(encoding="utf-8"))
    else:
        baseline = {}

    existing = baseline.get(provider)
    if existing is None:
        baseline[provider] = version
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        return True, None
    if existing == version:
        return False, None
    # Drift: keep the original baseline so later runs continue to see drift.
    return False, existing
