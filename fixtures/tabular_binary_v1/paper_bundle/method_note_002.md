# Method Note 002 — Stacked Generalization with Diverse Base Learners

Source: trusted local method note, hand-written for the Phase 0 fixture.
Trusted status: trusted_fixture.

## Mechanism

Train two or more base learners with substantially different inductive biases (e.g. a linear model and a tree-based model), generate out-of-fold (OOF) predictions for each on the training set, and feed those OOF predictions as features into a small meta-learner (typically logistic regression) that learns how to weight them. At inference time, base learners predict on the test set and the meta-learner combines their outputs.

The "diversity" requirement is the key: stacking only buys you signal if the base learners make different mistakes. Two GBDT models with different seeds rarely improve over one. A linear model + a GBDT often does, because they fail on different rows.

## Why it might help on tabular_binary_v1

The fixture is small (30 training rows, 20 test rows). Any single model is at high risk of overfitting noise. A linear baseline (logistic regression on numeric features + one-hot `cat`) and a tree baseline (single shallow decision tree, depth ≤ 3) bring complementary biases:
- Linear: smooth decision boundary, no interaction modeling unless explicitly constructed.
- Tree: piecewise-constant boundary, captures interactions but partitions tiny samples poorly.

Their OOF predictions, fed into a logistic meta-learner, can hedge against either's worst case.

## Assumptions

- Base learners are sufficiently diverse that their predictions are not strongly correlated.
- The meta-learner sees only OOF base predictions, never in-fold predictions (otherwise leakage produces over-optimistic CV).
- The training set has enough rows for k-fold cross-validation (k=3 or k=5) to produce meaningful OOF estimates. With 30 rows and k=5, each fold has 24 train / 6 validate — small but workable for a sanity check.

## Failure modes

- Leakage from improperly held-out OOF predictions silently inflates CV scores by 0.05–0.20 AUC; the held-out test score then collapses.
- With very small datasets, the meta-learner can overfit to OOF noise. Regularization (e.g. C=0.1 on the LR meta) helps.
- If base learners are not diverse, stacking adds variance for no gain.

## Implementation clues

- Use `sklearn.model_selection.KFold(n_splits=5, shuffle=True, random_state=42)` for the OOF split.
- Base 1: `sklearn.linear_model.LogisticRegression(max_iter=1000, C=1.0)` with one-hot encoded `cat`.
- Base 2: `sklearn.tree.DecisionTreeClassifier(max_depth=3, random_state=42)`.
- Meta: `sklearn.linear_model.LogisticRegression(C=0.1)`.
- Test predictions: each base trained on full training set, meta combines.

## Metrics to track

- ROC-AUC of the stack vs. each base alone (on a held-out fold of `train.csv`).
- Correlation of base predictions on OOF (lower = more diverse = more potential gain).
- Log-loss to verify probabilistic calibration is not degraded.

## References

This note is hand-written for the fixture and does not cite an external paper. In a real research-fusion run, citations from a trusted source ledger would be supplied.
