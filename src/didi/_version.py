"""Version and build information for git-didi."""

from functools import cache
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from subprocess import run


@cache
def get_version() -> str:
    """Get the package version from metadata."""
    try:
        return version("git-didi")
    except PackageNotFoundError:
        return "unknown"


@cache
def get_git_sha() -> str | None:
    """Get the git SHA, either from build info or live git repo.

    For editable installs, queries git directly.
    For PyPI installs, returns the SHA embedded at build time (if available).
    """
    # First, try to get SHA from the package directory (editable install)
    pkg_dir = Path(__file__).parent.parent
    git_dir = pkg_dir / ".git"

    if git_dir.exists() or (pkg_dir / "..").resolve().joinpath(".git").exists():
        # We're in a git repo (editable install)
        try:
            result = run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=pkg_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except FileNotFoundError:
            pass

    # Check if SHA was embedded at build time
    try:
        from didi._build_info import BUILD_SHA
        return BUILD_SHA
    except ImportError:
        pass

    return None


@cache
def get_version_info() -> str:
    """Get full version info string including SHA if available."""
    ver = get_version()
    sha = get_git_sha()
    if sha:
        return f"{ver} (git: {sha})"
    return ver
