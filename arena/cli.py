from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest

app = typer.Typer(help='Kaggle Agent Arena Phase 0 harness CLI.')
console = Console()


@app.command()
def doctor() -> None:
    """Run lightweight local readiness checks."""
    validate_fixture_manifest('fixtures/tabular_binary_v1')
    console.print('[green]arena doctor passed[/green]')


@app.command('fixture-smoke')
def fixture_smoke(
    submission: str = 'fixtures/tabular_binary_v1/sample_submission.csv',
    labels: str = 'fixtures/tabular_binary_v1/hidden_labels.csv',
) -> None:
    """Evaluate the bundled fake tabular fixture submission."""
    result = evaluate_fixture_submission(submission, labels)
    if not result.valid_submission:
        raise typer.Exit(code=1)
    console.print(f'fixture score={result.score:.6f}')


if __name__ == '__main__':
    app()
