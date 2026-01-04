"""Test diff utilities."""

import os
import tempfile

from didi.diff import (
    build_diff_cmd,
    compute_upstream_range,
    expand_refspec,
    normalize_diff,
    parse_paths,
    parse_refspec_bases,
    parse_refspec_endpoints,
    run_pipeline,
    REFSPEC_SENTINELS,
)


def test_build_diff_cmd_basic():
    """Test basic diff command building."""
    cmd = build_diff_cmd()
    assert cmd == ['git', 'diff']


def test_build_diff_cmd_with_follow():
    """Test diff command with --follow."""
    cmd = build_diff_cmd(follow=True)
    assert cmd == ['git', 'diff', '--follow']


def test_build_diff_cmd_with_ignore_whitespace():
    """Test diff command with -w flag."""
    cmd = build_diff_cmd(ignore_whitespace=True)
    assert cmd == ['git', 'diff', '-w']


def test_build_diff_cmd_with_renames():
    """Test diff command with -M flag."""
    cmd = build_diff_cmd(find_renames='50')
    assert cmd == ['git', 'diff', '-M50']


def test_build_diff_cmd_with_copies():
    """Test diff command with -C flag."""
    cmd = build_diff_cmd(find_copies='50')
    assert cmd == ['git', 'diff', '-C50']


def test_build_diff_cmd_all_options():
    """Test diff command with all options."""
    cmd = build_diff_cmd(
        ignore_whitespace=True,
        find_renames='50',
        find_copies='60',
        follow=True,
    )
    assert cmd == ['git', 'diff', '--follow', '-w', '-M50', '-C60']


def test_normalize_diff():
    """Test diff normalization removes index SHAs."""
    diff_text = """diff --git a/file.py b/file.py
index abc123..def456 100644
--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
-old line
+new line
"""
    normalized = normalize_diff(diff_text)
    assert 'abc123' not in normalized
    assert 'def456' not in normalized
    assert 'index ...' in normalized
    assert 'old line' in normalized
    assert 'new line' in normalized


def test_normalize_diff_preserves_non_index_lines():
    """Test that normalization preserves other diff content."""
    diff_text = """diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,1 +1,1 @@
-old content
+new content"""
    normalized = normalize_diff(diff_text)
    assert normalized == diff_text


def test_parse_refspec_bases():
    """Test parsing refspecs to extract bases."""
    base1, tip1, base2, tip2 = parse_refspec_bases('A..B', 'C..D')
    assert base1 == 'A'
    assert tip1 == 'B'
    assert base2 == 'C'
    assert tip2 == 'D'


def test_parse_refspec_bases_invalid():
    """Test parsing invalid refspecs."""
    result = parse_refspec_bases('A', 'B')
    assert result == ('', '', '', '')


def test_compute_upstream_range():
    """Test computing upstream range from two refspecs."""
    upstream = compute_upstream_range('main..feature', 'upstream/main..feature-rebased')
    assert upstream == 'main..upstream/main'


def test_compute_upstream_range_invalid():
    """Test computing upstream range with invalid refspecs."""
    upstream = compute_upstream_range('main', 'feature')
    assert upstream == ''


# Tests for parse_refspec_endpoints


def test_parse_refspec_endpoints_range():
    """Test parsing refspec with .. notation."""
    start, end = parse_refspec_endpoints('A..B')
    assert start == 'A'
    assert end == 'B'


def test_parse_refspec_endpoints_single():
    """Test parsing single commit refspec."""
    start, end = parse_refspec_endpoints('HEAD')
    assert start == 'HEAD^'
    assert end == 'HEAD'


# Tests for parse_paths


def test_parse_paths_direct():
    """Test parsing direct path arguments."""
    paths = parse_paths(('file1.txt', 'file2.txt'))
    assert paths == ['file1.txt', 'file2.txt']


def test_parse_paths_from_file():
    """Test parsing paths from @file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write('path1.txt\npath2.txt\n  path3.txt  \n')
        f.flush()
        try:
            paths = parse_paths((f'@{f.name}',))
            assert paths == ['path1.txt', 'path2.txt', 'path3.txt']
        finally:
            os.unlink(f.name)


def test_parse_paths_mixed():
    """Test parsing mixed direct paths and @file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write('from_file.txt\n')
        f.flush()
        try:
            paths = parse_paths(('direct.txt', f'@{f.name}'))
            assert paths == ['direct.txt', 'from_file.txt']
        finally:
            os.unlink(f.name)


# Tests for run_pipeline


def test_run_pipeline_single_command():
    """Test running a single command."""
    output = run_pipeline(['echo hello'])
    assert output.strip() == 'hello'


def test_run_pipeline_multi_stage():
    """Test running multiple piped commands."""
    output = run_pipeline(['echo "b\na\nc"', 'sort'])
    assert output.strip() == 'a\nb\nc'


def test_run_pipeline_empty():
    """Test running empty pipeline."""
    output = run_pipeline([])
    assert output == ''


# Tests for expand_refspec


def test_expand_refspec_passthrough():
    """Test that non-sentinel refspecs pass through unchanged."""
    assert expand_refspec('HEAD~5..HEAD') == 'HEAD~5..HEAD'
    assert expand_refspec('main..feature') == 'main..feature'
    assert expand_refspec('abc123') == 'abc123'


def test_refspec_sentinels_defined():
    """Test that expected sentinels are defined."""
    assert 'mboh' in REFSPEC_SENTINELS
    assert 'mbot' in REFSPEC_SENTINELS
    assert 'mbrhp' in REFSPEC_SENTINELS
    assert 'mbrp' in REFSPEC_SENTINELS  # alias for mbrhp
    assert 'oth' in REFSPEC_SENTINELS
