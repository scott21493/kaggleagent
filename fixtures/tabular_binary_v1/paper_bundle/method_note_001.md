# Method Note 001 — Monotonic Gradient-Boosted Decision Trees for Tabular Binary Classification

Source: trusted local method note, hand-written for the Phase 0 fixture.
Trusted status: trusted_fixture (the controller may treat this as a benign source).

## Mechanism

Train a gradient-boosted decision tree (GBDT) ensemble where each tree's split decisions are constrained to be monotonic with respect to a designated feature. Concretely, for a feature `x_k` declared monotone-increasing, every split on `x_k` must place rows with larger `x_k` values into the right child, and the predicted log-odds of the positive class must be a non-decreasing function of `x_k` holding all other features fixed. This is enforced during split-gain calculation: candidate splits that would violate the monotonicity constraint are rejected before scoring.

## Why it might help on tabular_binary_v1

The fixture has two numeric features (`x1`, `x2`) and one low-cardinality categorical feature (`cat`). Looking at `train.csv`, larger `x1` values are weakly associated with target=1, and `x2` shows a more pronounced pattern. If we believe the relationship between `x2` and the target is genuinely monotonic in the underlying data-generating process, a monotonic constraint would reduce variance from spurious non-monotone splits in small training sets without sacrificing meaningful signal.

## Assumptions

- The relationship between the constrained feature(s) and target log-odds is in fact monotonic in the population, not just on the training sample.
- The training set is too small for a free-form GBDT to learn the monotone shape reliably without overfitting noise.
- Categorical features are handled by ordinal encoding or native categorical support (LightGBM, CatBoost).

## Failure modes

- If monotonicity is incorrectly assumed, the constrained model can underperform an unconstrained one because it cannot represent legitimate non-monotone interactions.
- If the wrong feature is constrained (e.g. constraining `x1` when only `x2` is monotone), expect degraded log-loss and AUC.
- Small training sets can produce misleading monotone-vs-not comparisons; ablations need bootstrap or cross-validated comparisons, not single-fold reads.

## Implementation clues

- LightGBM: pass `monotone_constraints=[0, 1, 0]` to constrain feature index 1 (`x2`) to monotone-increasing.
- XGBoost: pass `monotone_constraints="(0,1,0)"` (string form) for the same effect.
- For the fixture, an ablation that flips between `monotone_constraints=[0, 0, 0]` (free) and `[0, 1, 0]` (constrained on `x2`) is the smallest meaningful experiment.

## Metrics to track

- ROC-AUC on a held-out fold of `train.csv` (the only labeled data available; `hidden_labels.csv` is evaluator-only).
- Log-loss on the same fold.
- Number of leaves per tree, average tree depth (proxy for whether the constraint binds).

## References

This note is hand-written for the fixture and does not cite an external paper. In a real research-fusion run, the controller would supply citations from a trusted source ledger.
