"""Write the stats block at the bottom of README.md from the committed tree.

python docs/stats.py 0.5.3 [--downloads N]
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import tomllib
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
START = "<!-- stats:start -->"
END = "<!-- stats:end -->"
# What the release was tried on, not what the code merely recognises.
TESTED_BROWSERS = ("Chrome", "Chromium", "Brave", "Firefox", "Zen")
INSTALL_WAYS = ("PyPI", "AUR", "AppImage")

SKIP_TOKENS = {
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def head_files() -> dict[str, bytes]:
    paths = [p for p in git("ls-tree", "-r", "-z", "--name-only", "HEAD").split("\0") if p]
    # One cat-file process instead of one `git show` per file.
    query = "".join(f"HEAD:{p}\n" for p in paths).encode()
    out = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=REPO, input=query, check=True, capture_output=True
    ).stdout
    files: dict[str, bytes] = {}
    pos = 0
    for path in paths:
        header_end = out.index(b"\n", pos)
        header = out[pos:header_end].split()
        size = int(header[2])
        start = header_end + 1
        files[path] = out[start : start + size]
        pos = start + size + 1
    return files


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def count_words(data: bytes) -> int:
    return len(data.split())


def count_lines(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def count_code_lines(source: str) -> int:
    """Lines holding code: no blank lines, comment-only lines or docstrings."""
    code: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in SKIP_TOKENS:
            code.update(range(tok.start[0], tok.end[0] + 1))
    docs: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            doc = node.body[0]
            docs.update(range(doc.lineno, doc.end_lineno + 1))
    return len(code - docs)


def count_test_defs(sources: list[str]) -> int:
    return sum(len(re.findall(r"^\s*(?:async\s+)?def test_", s, re.M)) for s in sources)


def parse_collected(output: str) -> int:
    # pyproject's addopts already has -q, so the extra -q prints "file.py: N" per file
    # instead of one node id per line.
    total = 0
    for line in output.splitlines():
        if "::" in line:
            total += 1
        elif m := re.fullmatch(r"\S+\.py: (\d+)", line.strip()):
            total += int(m.group(1))
    return total


def run_pytest(*args: str) -> subprocess.CompletedProcess:
    python = REPO / ".venv" / "bin" / "python"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [str(python if python.exists() else sys.executable), "-m", "pytest"]
        + ["-q", "-p", "no:cacheprovider", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )


def collect_tests(files: dict[str, bytes]) -> int:
    result = run_pytest("--collect-only")
    collected = parse_collected(result.stdout)
    if result.returncode == 0 and collected:
        return collected
    return count_test_defs(
        [d.decode("utf-8", "replace") for p, d in files.items() if p.startswith("tests/")]
    )


def coverage_percent() -> int | None:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "coverage.json"
        result = run_pytest(
            "--cov=refrain",
            f"--cov-report=json:{report}",
            f"--cov-config={REPO / 'pyproject.toml'}",
        )
        if result.returncode != 0 or not report.exists():
            print("warning: coverage row left out, the test run failed", file=sys.stderr)
            return None
        return percent_covered(json.loads(report.read_text(encoding="utf-8")))


def percent_covered(report: dict) -> int:
    return round(report["totals"]["percent_covered"])


def listed(names: tuple[str, ...]) -> str:
    return f"{len(names)} ({', '.join(names)})"


def languages(paths: list[str]) -> int:
    codes = {
        m.group(1) for p in paths if (m := re.fullmatch(r"src/refrain/i18n/refrain_(\w+)\.ts", p))
    }
    return len(codes | {"en"})


def dependencies(pyproject: bytes) -> int:
    return len(tomllib.loads(pyproject.decode())["project"]["dependencies"])


def render(version: str, stats: list[tuple[str, int | str]]) -> str:
    rows = "\n".join(
        f"| {name} | {value:,} |" if isinstance(value, int) else f"| {name} | {value} |"
        for name, value in stats
    )
    return f"{START}\n## Stats\n\n| Stat | Value |\n|---|---|\n{rows}\n\nAs of v{version}.\n{END}"


def replace_block(text: str, block: str) -> str:
    start, end = text.find(START), text.find(END)
    if start != -1 and end != -1:
        return text[:start] + block + text[end + len(END) :]
    return text.rstrip("\n") + "\n\n" + block + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument(
        "--downloads",
        type=int,
        help="PyPI (last 180 days) plus GitHub release downloads, looked up by hand",
    )
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    files = head_files()
    text_files = {p: d for p, d in files.items() if not is_binary(d)}
    tags = dict(
        line.split()
        for line in git(
            "for-each-ref", "--format=%(refname:short) %(creatordate:short)", "refs/tags/v*"
        ).splitlines()
    )
    # The release commit carrying this block is made before its tag exists.
    tags.setdefault(f"v{version}", date.today().isoformat())
    stats: list[tuple[str, int | str]] = [
        (
            "Lines of code",
            sum(
                count_code_lines(d.decode())
                for p, d in text_files.items()
                if p.startswith("src/") and p.endswith(".py")
            ),
        ),
        ("Lines in the repository", sum(count_lines(d) for d in text_files.values())),
        ("Words in the repository", sum(count_words(d) for d in text_files.values())),
        (
            "Words of documentation",
            sum(count_words(d) for p, d in text_files.items() if p.endswith(".md")),
        ),
        ("Automated tests", collect_tests(files)),
        (
            "Days since the first release",
            (date.today() - date.fromisoformat(min(tags.values()))).days,
        ),
        ("Versions released", len(tags)),
        ("Commits", int(git("rev-list", "--count", "HEAD"))),
        ("Languages", languages(list(files))),
        ("Runtime dependencies", dependencies(files["pyproject.toml"])),
        ("Browsers tested", listed(TESTED_BROWSERS)),
        ("Ways to install", listed(INSTALL_WAYS)),
    ]
    coverage = coverage_percent()
    if coverage is not None:
        names = [name for name, _ in stats]
        stats.insert(names.index("Automated tests") + 1, ("Test coverage", f"{coverage} %"))
    if args.downloads is not None:
        stats.insert(
            stats.index(("Versions released", len(tags))) + 1, ("Downloads", args.downloads)
        )
    block = render(version, stats)
    text = README.read_text(encoding="utf-8")
    README.write_text(replace_block(text, block), encoding="utf-8")
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
