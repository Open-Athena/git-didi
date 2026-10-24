"""Integration tests for rebase-related refspec handling."""

import os
import subprocess
import tempfile

import pytest

from didi.diff import (
    expand_refspec,
    get_onto,
    get_orig_head,
    get_rebase_merge_base,
    get_rebase_refspecs,
)


def run_git(*args, cwd=None):
    """Run a git command and return stdout."""
    result = subprocess.run(
        ['git', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


@pytest.fixture
def rebase_repo(tmp_path):
    """Create a repo with a completed rebase for testing.

    Creates:
        A---B  (feature, rebased)
       /
      F---G---H  (main)
         /
        A'--B'  (feature after rebase)

    Where:
      - F is the initial commit (base)
      - G is merge-base (where feature diverged)
      - H is main tip (onto)
      - A, B are original feature commits
      - A', B' are rebased feature commits
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    cwd = str(repo)

    # Initialize repo with base commit
    run_git('init', '-b', 'main', cwd=cwd)
    run_git('config', 'user.email', 'test@test.com', cwd=cwd)
    run_git('config', 'user.name', 'Test', cwd=cwd)

    (repo / "file.txt").write_text("base\n")
    run_git('add', 'file.txt', cwd=cwd)
    run_git('commit', '-m', 'F: base', cwd=cwd)

    # G: merge-base commit
    (repo / "file.txt").write_text("base\nG\n")
    run_git('commit', '-am', 'G: merge-base', cwd=cwd)
    merge_base_sha = run_git('rev-parse', 'HEAD', cwd=cwd)

    # Create feature branch with A, B commits
    run_git('checkout', '-b', 'feature', cwd=cwd)
    (repo / "feature.txt").write_text("A\n")
    run_git('add', 'feature.txt', cwd=cwd)
    run_git('commit', '-m', 'A: first feature', cwd=cwd)

    (repo / "feature.txt").write_text("A\nB\n")
    run_git('commit', '-am', 'B: second feature', cwd=cwd)
    orig_head_sha = run_git('rev-parse', 'HEAD', cwd=cwd)

    # Back to main, add H commit
    run_git('checkout', 'main', cwd=cwd)
    (repo / "file.txt").write_text("base\nG\nH\n")
    run_git('commit', '-am', 'H: main tip', cwd=cwd)
    onto_sha = run_git('rev-parse', 'HEAD', cwd=cwd)

    # Rebase feature onto main
    run_git('checkout', 'feature', cwd=cwd)
    run_git('rebase', 'main', cwd=cwd)
    head_sha = run_git('rev-parse', 'HEAD', cwd=cwd)

    return {
        'cwd': cwd,
        'merge_base': merge_base_sha,
        'orig_head': orig_head_sha,
        'onto': onto_sha,
        'head': head_sha,
    }


class TestRebaseHelpers:
    """Test helper functions that extract rebase state."""

    def test_get_orig_head(self, rebase_repo, monkeypatch):
        """Test get_orig_head returns correct SHA after rebase."""
        monkeypatch.chdir(rebase_repo['cwd'])
        assert get_orig_head() == rebase_repo['orig_head']

    def test_get_onto(self, rebase_repo, monkeypatch):
        """Test get_onto returns correct SHA after rebase (from reflog)."""
        monkeypatch.chdir(rebase_repo['cwd'])
        assert get_onto() == rebase_repo['onto']

    def test_get_rebase_merge_base(self, rebase_repo, monkeypatch):
        """Test get_rebase_merge_base returns correct SHA."""
        monkeypatch.chdir(rebase_repo['cwd'])
        assert get_rebase_merge_base() == rebase_repo['merge_base']


class TestSentinelExpansion:
    """Test sentinel refspec expansion after rebase."""

    def test_expand_mboh(self, rebase_repo, monkeypatch):
        """Test mboh expands to merge-base..orig-head."""
        monkeypatch.chdir(rebase_repo['cwd'])
        result = expand_refspec('mboh')
        expected = f"{rebase_repo['merge_base']}..{rebase_repo['orig_head']}"
        assert result == expected

    def test_expand_oth(self, rebase_repo, monkeypatch):
        """Test oth expands to onto..HEAD."""
        monkeypatch.chdir(rebase_repo['cwd'])
        result = expand_refspec('oth')
        expected = f"{rebase_repo['onto']}..HEAD"
        assert result == expected

    def test_expand_mbot(self, rebase_repo, monkeypatch):
        """Test mbot expands to merge-base..onto."""
        monkeypatch.chdir(rebase_repo['cwd'])
        result = expand_refspec('mbot')
        expected = f"{rebase_repo['merge_base']}..{rebase_repo['onto']}"
        assert result == expected

    def test_get_rebase_refspecs(self, rebase_repo, monkeypatch):
        """Test get_rebase_refspecs returns mboh and oth."""
        monkeypatch.chdir(rebase_repo['cwd'])
        refspec1, refspec2 = get_rebase_refspecs()
        expected1 = f"{rebase_repo['merge_base']}..{rebase_repo['orig_head']}"
        expected2 = f"{rebase_repo['onto']}..HEAD"
        assert refspec1 == expected1
        assert refspec2 == expected2
