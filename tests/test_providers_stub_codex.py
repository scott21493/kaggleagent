from __future__ import annotations

from pathlib import Path

import pandas as pd

from arena.controller.planner import create_calibration_task_packet
from arena.providers.stub_codex import StubCodexProvider
from arena.schemas.validate import validate


def test_invoke_writes_valid_submission(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001"
    workspace.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    # Need test.csv at the path the planner references; copy from repo fixture.
    fixture_src = Path(__file__).resolve().parents[1] / "fixtures" / "tabular_binary_v1"
    target_dir = tmp_path / "fixtures" / "tabular_binary_v1"
    target_dir.mkdir(parents=True)
    for name in ["train.csv", "test.csv", "sample_submission.csv", "competition.yaml", "rules.md"]:
        (target_dir / name).write_bytes((fixture_src / name).read_bytes())

    packet = create_calibration_task_packet(
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    provider = StubCodexProvider(workspace_root=tmp_path / "worktrees")
    result = provider.invoke(packet)

    validate("provider_result", result.to_dict())
    assert result.status == "success"
    submission_path = workspace / "submission.csv"
    assert submission_path.exists()
    df = pd.read_csv(submission_path)
    assert list(df.columns) == ["id", "target"]
    assert df["target"].between(0, 1).all()
    test_df = pd.read_csv(target_dir / "test.csv")
    assert len(df) == len(test_df)
