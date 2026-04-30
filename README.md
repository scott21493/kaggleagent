# Kaggle Agent Arena V2

This pack is the **single-scope Phase 0** implementation plan and runnable skeleton for the Kaggle Agent Arena harness.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install '.[dev]'
arena doctor
python scripts/fixture_smoke.py
pytest --cov=arena --cov-report=term-missing -q
```

Phase 0 uses stub providers in CI. Real Codex/Claude subscription providers are optional local-only integrations and are not required for the smoke test.

## Read first

1. `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md`
2. `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md`
3. `docs/architecture/KAGGLE_AGENT_ARENA_DESIGN_V2.md`
4. `docs/memory/UNIFIED_MEMORY_WIKI.md`

## Non-goals for Phase 0

- no real Kaggle submissions;
- no API-based model calls;
- no full paper ingestion engine;
- no multi-adapter abstraction;
- no autonomous mutation of protected controller/provider/security files.
