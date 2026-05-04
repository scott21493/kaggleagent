from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.providers.parser import build_result
from arena.schemas.validate import validate

_VERSION = "stub_claude.v1"

# PR6 stub review defaults — monkey-patchable by tests for rejection
# paths. Not exposed in the schema; just stub knobs.
_RESEARCH_REVIEW_DEFAULT_DECISION = "accept"
_RESEARCH_REVIEW_DEFAULT_RISK_LEVEL = "low"
_RESEARCH_REVIEW_DEFAULT_REQUIRED_FIXES: list[str] = []


class StubClaudeProvider(ProviderAdapter):
    """Deterministic stand-in for Claude during Phase 0 CI and local stub runs.

    PR1 ships the calibration skeleton (no artifacts). PR5 extends invoke()
    to dispatch on (role, phase): research_proxy + (RESEARCH_QUESTION_CREATED,
    METHOD_DIGEST_CREATED, FUSION_PROPOSAL_CREATED) phases write a
    schema-valid JSON artifact. PR6 extends with role=review +
    FUSION_PROXY_REVIEWED, which emits research_review.json. (The
    MEMORY_PROPOSAL_CREATED phase exists in the Phase enum, but PR6's
    arena memory propose is a controller-only action — no provider
    invocation, no stub_claude dispatch — so this provider does NOT
    handle that phase.)

    Optional fields exercise observability: failed_commands is a list of
    (command_str, exit_code) pairs that the stub emits as
    shell_command_observed events through `event_emitter` before producing
    its normal result. Enables PR4's live waste-detector path tests
    (security acceptance test 5).
    """

    def __init__(
        self,
        workspace_root: str | Path = "worktrees",
        *,
        event_emitter: TraceStore | None = None,
        failed_commands: list[tuple[str, int]] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._event_emitter = event_emitter
        self._failed_commands = failed_commands or []

    @property
    def name(self) -> str:
        return "stub_claude"

    @property
    def version(self) -> str:
        return _VERSION

    def invoke(self, task_packet: dict) -> ProviderResult:
        validate("task_packet", task_packet)
        # PR4 live waste path: emit shell_command_observed events for any
        # injected failed_commands.
        if self._event_emitter is not None:
            for command, exit_code in self._failed_commands:
                self._event_emitter.emit(
                    event_type="shell_command_observed",
                    severity="info" if exit_code == 0 else "warning",
                    task_id=task_packet["task_id"],
                    payload={"command": command, "exit_code": exit_code},
                )
        slug = task_packet["competition_slug"]
        exp_id = task_packet["experiment_id"]
        if exp_id is None:
            raise ValueError("StubClaudeProvider requires task_packet.experiment_id to be set")
        task_id = task_packet["task_id"]

        workspace = self._workspace_root / slug / exp_id
        workspace.mkdir(parents=True, exist_ok=True)
        stdout_path = workspace / f"{task_id}.stub_claude.stdout.scrubbed"
        stderr_path = workspace / f"{task_id}.stub_claude.stderr.scrubbed"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")

        # PR5+PR6 dispatch: research_proxy + review roles emit a
        # schema-valid artifact under the workspace.
        artifacts: list[str] = []
        role = task_packet["role"]
        phase = task_packet["phase"]
        payload: dict[str, Any] | None = None
        artifact_name: str | None = None
        if role == "research_proxy":
            payload, artifact_name = self._research_proxy_payload(slug, phase)
        elif role == "review":
            payload, artifact_name = self._review_payload(slug, phase, task_packet["inputs"])
        if payload is not None and artifact_name is not None:
            artifact_path = workspace / artifact_name
            artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            artifacts.append(str(artifact_path))

        now = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            artifacts=artifacts,
            input_chars=0,
            output_chars=sum(Path(p).stat().st_size for p in artifacts),
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=now,
            finished_at=now,
        )

    def _research_proxy_payload(
        self, slug: str, phase: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return (payload, artifact_name) for the research-proxy phase, or
        (None, None) if the phase doesn't require artifact emission. Each
        payload is deterministic and schema-valid by construction."""
        if phase == "RESEARCH_QUESTION_CREATED":
            return self._research_question_payload(slug), "research_question.json"
        if phase == "METHOD_DIGEST_CREATED":
            return self._paper_digest_payload(slug), "paper_digest.json"
        if phase == "FUSION_PROPOSAL_CREATED":
            return self._fusion_proposal_payload(slug), "fusion_proposal.json"
        return None, None

    def _research_question_payload(self, slug: str) -> dict[str, Any]:
        return {
            "schema_version": "research_question.v1",
            "question_id": "rq_0001",
            "competition_slug": slug,
            "question": (
                "Does combining a monotonic GBDT with a stacked logistic-regression "
                "meta-learner reduce variance on the small tabular_binary_v1 fixture "
                "compared to a free-form GBDT baseline?"
            ),
            "motivation": (
                "Method note 001 argues monotonic constraints reduce variance on "
                "small training sets; method note 002 argues stacked diverse base "
                "learners reduce bias. The fixture is small (50 rows) so variance "
                "dominates — the combination should outperform either alone."
            ),
            "expected_mechanisms": [
                "monotonic gradient-boosted decision trees",
                "stacked logistic-regression meta-learner",
            ],
            "expected_cost": "small",
            "risk": "low",
            "smallest_test": (
                "5-fold CV on train.csv comparing baseline GBDT vs monotonic-GBDT "
                "+ stacked-LR ensemble. Report ROC-AUC mean + std; pass if the "
                "ensemble's mean is at least 0.02 above baseline."
            ),
            "stop_condition": (
                "Stop if the ensemble's CV mean is below baseline by more than 0.01 "
                "or training wall time exceeds 5 minutes per fold."
            ),
            "source_refs": [
                "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
                "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
            ],
        }

    def _paper_digest_payload(self, slug: str) -> dict[str, Any]:
        return {
            "schema_version": "paper_digest.v1",
            "digest_id": "pd_0001",
            "source_id": "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
            "title": (
                "Monotonic Gradient-Boosted Decision Trees for Tabular Binary Classification"
            ),
            "source_type": "local_method_note",
            "trusted_status": "trusted_fixture",
            "mechanisms": [
                {
                    "name": "monotonic_gbdt",
                    "description": (
                        "Gradient-boosted decision tree ensemble where splits on a "
                        "designated feature are constrained to produce monotonically "
                        "non-decreasing log-odds with respect to that feature."
                    ),
                    "why_it_might_help": (
                        "Reduces variance from spurious non-monotone splits in small "
                        "training sets without sacrificing meaningful signal when "
                        "the target relationship is genuinely monotonic."
                    ),
                }
            ],
            "assumptions": [
                "Target log-odds is monotonic in the constrained feature in the population.",
                "Training set is small enough that variance dominates bias.",
            ],
            "datasets_or_tasks": ["tabular_binary_v1"],
            "metrics": ["ROC-AUC"],
            "implementation_clues": [
                "LightGBM monotone_constraints parameter",
                "CatBoost monotone_constraints parameter",
            ],
            "failure_modes": [
                "True relationship is non-monotonic; constraint kills useful signal.",
                "Wrong feature is constrained.",
            ],
            "applicability": {
                "competition_slug": slug,
                "fit": "high",
                "reason": (
                    "Fixture has continuous numeric features with plausibly monotonic "
                    "relationship to target; small training set magnifies variance, "
                    "which monotonic constraints address directly."
                ),
            },
            "citations": [
                {
                    "ref": "method_note_001.md",
                    "summary": (
                        "Local trusted method note describing monotonic GBDTs for "
                        "tabular binary classification on small datasets."
                    ),
                }
            ],
        }

    def _fusion_proposal_payload(self, slug: str) -> dict[str, Any]:
        return {
            "schema_version": "fusion_proposal.v1",
            "fusion_id": "fusion_0001",
            "competition_slug": slug,
            "title": "Monotonic-GBDT + Stacked Logistic-Regression Meta-Learner",
            "hypothesis": (
                "On the tabular_binary_v1 fixture, combining a monotonic GBDT base "
                "learner with a stacked logistic-regression meta-learner over OOF "
                "predictions will reduce CV ROC-AUC variance compared to either "
                "method alone, by simultaneously addressing variance (monotonic "
                "constraints) and bias (diverse stacked learners)."
            ),
            "mechanisms_combined": [
                {
                    "mechanism_name": "monotonic_gbdt",
                    "source_ref": "method_note_001.md",
                    "role_in_fusion": (
                        "Acts as the variance-reducing base learner with monotonic "
                        "constraints on x2 (the most predictive feature)."
                    ),
                },
                {
                    "mechanism_name": "stacked_logistic_regression",
                    "source_ref": "method_note_002.md",
                    "role_in_fusion": (
                        "Acts as the meta-learner combining OOF predictions from the "
                        "monotonic GBDT with predictions from a linear logistic "
                        "regression base learner to add diversity."
                    ),
                },
            ],
            "implementation_plan": {
                "files_to_create_or_modify": [
                    "submission.csv",
                ],
                "algorithm_steps": [
                    "Load train.csv and test.csv from fixtures/<slug>/.",
                    (
                        "Train a monotonic GBDT (LightGBM, monotone_constraints "
                        "applied to x2) with 5-fold CV; collect OOF predictions on "
                        "the train set and full-train predictions on the test set."
                    ),
                    (
                        "Train a logistic regression base learner with the same 5 "
                        "folds; collect OOF train predictions and full-train test "
                        "predictions."
                    ),
                    (
                        "Stack OOF predictions as features for a meta logistic "
                        "regression on the train labels; predict the test set "
                        "stacked features to produce final probabilities."
                    ),
                    "Write submission.csv with columns id, target.",
                ],
                "dependencies": ["lightgbm>=4.0", "scikit-learn>=1.3", "pandas>=2.0"],
                "expected_outputs": ["submission.csv"],
            },
            "smallest_proxy_test": {
                "description": (
                    "5-fold CV on train.csv with both base learners + stacked meta. "
                    "Report mean ROC-AUC vs the calibration baseline of 0.5 constant."
                ),
                "dataset_slice": "train",
                "metric": "roc_auc",
                "success_threshold": {
                    "metric": "roc_auc",
                    "comparator": ">=",
                    "value": 0.5,
                },
                "max_runtime_minutes": 5,
            },
            "ablation_plan": [
                {
                    "name": "remove_monotonicity",
                    "remove_or_change": (
                        "Drop monotone_constraints from the GBDT; train without constraints."
                    ),
                    "expected_signal": (
                        "Variance increases; CV ROC-AUC std grows. Confirms the "
                        "monotonic constraint contributes."
                    ),
                },
                {
                    "name": "remove_stacking",
                    "remove_or_change": (
                        "Use only the monotonic GBDT base learner without the "
                        "stacked logistic regression."
                    ),
                    "expected_signal": (
                        "Bias increases; CV ROC-AUC mean drops. Confirms the stacking contributes."
                    ),
                },
            ],
            "resource_estimate": {
                "cost_class": "small",
                "gpu_required": False,
                "max_runtime_minutes": 10,
            },
            "risks": [
                "Target may be non-monotonic in x2; constraint hurts.",
                "Two base learners on 50 rows may overfit the meta-learner.",
            ],
            "stop_condition": (
                "Stop if CV ROC-AUC mean falls more than 0.05 below baseline OR "
                "training wall time exceeds 5 minutes per fold."
            ),
            "source_refs": [
                "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
                "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
            ],
        }

    def _review_payload(
        self, slug: str, phase: str, inputs: list[str]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return (payload, artifact_name) for the review phase, or
        (None, None) if the phase doesn't require artifact emission.

        The CLI passes the implementation row's submission.csv path as
        inputs[0]; we extract subject_id from its worktree path segment
        (`worktrees/<slug>/<exp>/submission.csv` → `<exp>`) the same way
        stub_codex extracts fusion_id from inputs[0] in PR5 Task 4.
        """
        if phase != "FUSION_PROXY_REVIEWED":
            return None, None
        subject_id = self._read_subject_id_from_inputs(inputs)
        if subject_id is None:
            return None, None
        return self._research_review_payload(slug, subject_id), "research_review.json"

    def _read_subject_id_from_inputs(self, inputs: list[str]) -> str | None:
        """Find the first input whose path matches
        `worktrees/<slug>/<exp_id>/submission.csv` and return <exp_id>.
        Returns None if no matching input exists or the path shape is
        unexpected.

        Assumes the Phase-0 CLI contract: input is a RELATIVE path of
        the form `worktrees/<slug>/<exp>/submission.csv` (Path.parts
        normalizes both forward- and back-slashes, so this works on
        Windows). Absolute paths and paths with `..` traversal segments
        produce parts whose [-4] element is not literally `"worktrees"`,
        and silently yield None. The CLI in PR6 Task 2 always passes
        relative paths from the scoreboard's artifact_paths column, so
        the silent-None branch is unreachable in practice.
        """
        for input_path in inputs:
            if not input_path.endswith("submission.csv"):
                continue
            parts = Path(input_path).parts
            # Expect ("worktrees", slug, exp_id, "submission.csv")
            if len(parts) >= 4 and parts[-4] == "worktrees":
                exp_id = parts[-2]
                if exp_id.startswith("exp_"):
                    return exp_id
        return None

    def _research_review_payload(self, slug: str, subject_id: str) -> dict[str, Any]:
        return {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": slug,
            "subject_id": subject_id,
            "decision": _RESEARCH_REVIEW_DEFAULT_DECISION,
            "summary": (
                f"Reviewed proxy implementation {subject_id} against the "
                "monotonic-GBDT + stacked-LR fusion. Submission.csv is "
                "schema-valid; pipeline integrity confirmed."
            ),
            "strengths": [
                "Submission shape matches sample_submission.csv columns.",
                "Proxy implementation completed within budget caps.",
            ],
            "weaknesses": [
                "Phase-0 stub produces a constant 0.5 baseline; "
                "no signal extracted from the fusion proposal.",
            ],
            "required_fixes": list(_RESEARCH_REVIEW_DEFAULT_REQUIRED_FIXES),
            "follow_up_recommendations": [
                "When run against real Codex, re-run with the same fusion "
                "and compare ROC-AUC against the calibration baseline.",
            ],
            "risk_level": _RESEARCH_REVIEW_DEFAULT_RISK_LEVEL,
        }
