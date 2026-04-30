from __future__ import annotations

# SCAFFOLDING: greps .env.example for required policy strings. Replace when arena/sandbox/ lands —
# must execute the runtime sandbox against the security acceptance tests in
# docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §9 (secret-read blocking, network egress
# blocking, protected-file enforcement).

from pathlib import Path

REQUIRED_PHRASES = [
    'ARENA_NETWORK_DEFAULT=deny',
    'ARENA_PHASE0_KAGGLE_SUBMISSIONS_ALLOWED=0',
    'KAGGLE_CONFIG_DIR=',
    'CODEX_HOME=',
    'CLAUDE_CONFIG_DIR=',
]


def main() -> None:
    text = Path('.env.example').read_text(encoding='utf-8')
    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in text]
    if missing:
        raise SystemExit(f'missing sandbox/env policy phrases: {missing}')
    print('sandbox policy static check passed')


if __name__ == '__main__':
    main()
