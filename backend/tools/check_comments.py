"""
Enforce the repo's comment rule: one line, saying what is done.

A comment that runs over several lines is always rationale, and rationale buries the logic it
sits on top of. The place for it is the docstring's `Note:` section, where it is one scroll
away instead of in the middle of a function.

Run by `make check`. Docstrings are skipped entirely — they are where prose belongs.
"""

import sys
from pathlib import Path

SKIP = {".venv", ".git", "__pycache__", "migrations", "node_modules"}


def offenders(path: Path) -> list[tuple[int, list[str]]]:
    """
    Find runs of consecutive comment lines in one file.

    Args:
        path (Path): The module to read.

    Returns:
        list[tuple[int, list[str]]]: Starting line and text of every run longer than one.

    Note:
        Docstrings are tracked by counting odd occurrences of the triple quote per line, which
        is enough for this repo's style and cannot be fooled by a quote inside a comment.
    """
    found = []
    in_docstring = False
    run: list[str] = []
    start = 0

    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.count('"""') % 2:
            in_docstring = not in_docstring
        if in_docstring:
            continue

        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            if not run:
                start = number
            run.append(stripped)
            continue

        if len(run) > 1:
            found.append((start, run))
        run = []

    if len(run) > 1:
        found.append((start, run))
    return found


def main(roots: list[str]) -> int:
    """
    Check every module under the given roots.

    Returns:
        int: 1 when any file carries a multi-line comment block, 0 otherwise.
    """
    failures = 0
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if SKIP & set(path.parts):
                continue
            for line, run in offenders(path):
                failures += 1
                print(f"{path}:{line}: multi-line comment; move the rationale to a docstring")
                for text in run:
                    print(f"    {text}")

    if failures:
        print(f"\n{failures} multi-line comment block(s). Comments are one line.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
