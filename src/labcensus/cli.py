import typer

app = typer.Typer(help="labcensus — read-only census of a lab's storage.")


@app.command()
def scan(path: str) -> None:
    """Walk PATH and emit a census report. (M0 — not yet implemented.)"""
    raise NotImplementedError


if __name__ == "__main__":
    app()
