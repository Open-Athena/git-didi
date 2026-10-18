"""Test patch splitting and normalization levels."""

from didi.normalize import (
    first_equal_level,
    level_label,
    line_counts,
    normalize,
    patch_paths,
    split_patch,
)


def patch(*body: str, index: str = 'index 1111111..2222222 100644', hunk: str = '@@ -10,3 +10,3 @@ def foo():') -> str:
    return '\n'.join([
        'diff --git a/f.py b/f.py',
        index,
        '--- a/f.py',
        '+++ b/f.py',
        hunk,
        *body,
    ])


BASE = patch(' a', '-b = 1', '+b = 2', ' c')


def test_normalize_none():
    assert normalize(BASE, 'none') == BASE.split('\n')


def test_normalize_index():
    assert normalize(BASE, 'index') == [
        'diff --git a/f.py b/f.py',
        'index ...',
        '--- a/f.py',
        '+++ b/f.py',
        '@@ -10,3 +10,3 @@ def foo():',
        ' a',
        '-b = 1',
        '+b = 2',
        ' c',
    ]


def test_normalize_lines():
    assert normalize(BASE, 'lines')[4:] == [
        '@@ def foo():',
        ' a',
        '-b = 1',
        '+b = 2',
        ' c',
    ]


def test_normalize_context():
    assert normalize(BASE, 'context')[4:] == ['-b = 1', '+b = 2']


def test_normalize_ws():
    assert normalize(BASE, 'ws')[4:] == ['-b=1', '+b=2']


def test_normalize_tokens():
    assert normalize(BASE, 'tokens')[4:] == ['replace: 1 → 2']


def test_normalize_keeps_content_lines_starting_with_index():
    p = patch(' a', '-index 1..2', '+index 3..4')
    assert normalize(p, 'index')[5:] == [' a', '-index 1..2', '+index 3..4']


def test_normalize_path_mapping():
    p = BASE.replace('f.py', 'old.py')
    assert normalize(p, 'index', {'old.py': 'new.py'})[:4] == [
        'diff --git a/new.py b/new.py',
        'index ...',
        '--- a/new.py',
        '+++ b/new.py',
    ]


def level_vs_base(other: str):
    return first_equal_level(BASE, other)


def test_first_equal_level_identical():
    assert level_vs_base(BASE) == 'none'


def test_first_equal_level_index():
    assert level_vs_base(patch(' a', '-b = 1', '+b = 2', ' c', index='index 3333333..4444444 100644')) == 'index'


def test_first_equal_level_lines():
    assert level_vs_base(patch(' a', '-b = 1', '+b = 2', ' c', hunk='@@ -51,3 +51,3 @@ def foo():')) == 'lines'


def test_first_equal_level_context():
    assert level_vs_base(patch(' A', '-b = 1', '+b = 2', ' c')) == 'context'


def test_first_equal_level_ws():
    assert level_vs_base(patch(' a', '-b  =  1', '+b = 2', ' c')) == 'ws'


def test_first_equal_level_tokens():
    """Same edit (append `, staged=True`) applied to a line the other side also changed."""
    left = patch('-def f(a, b):', '+def f(a, b, staged=True):')
    right = patch('-def f(a, b, c):', '+def f(a, b, c, staged=True):')
    assert first_equal_level(left, right) == 'tokens'
    assert normalize(right, 'tokens')[4:] == ['insert: , staged = True']


def test_first_equal_level_differs():
    assert level_vs_base(patch(' a', '-b = 1', '+b = 3', ' c')) is None


def test_level_label():
    assert [level_label(level) for level in ('none', 'index', 'lines', 'context', 'ws', 'tokens', None)] == [
        'equal', 'equal', 'equal@lines', 'equal@context', 'equal@ws', 'equal@tokens', 'differs',
    ]


def test_line_counts():
    assert line_counts(BASE) == (1, 1)
    assert line_counts(None) == (0, 0)


MULTI = '\n'.join([
    'diff --git a/mod.py b/mod.py',
    'index 1111111..2222222 100644',
    '--- a/mod.py',
    '+++ b/mod.py',
    '@@ -1 +1 @@',
    '-x',
    '+y',
    'diff --git a/gone.py b/gone.py',
    'deleted file mode 100644',
    'index 3333333..0000000',
    '--- a/gone.py',
    '+++ /dev/null',
    '@@ -1 +0,0 @@',
    '--- a comment',
    'diff --git a/old name.py b/new name.py',
    'similarity index 100%',
    'rename from old name.py',
    'rename to new name.py',
    'diff --git a/img.png b/img.png',
    'new file mode 100644',
    'index 0000000..4444444',
    'Binary files /dev/null and b/img.png differ',
    'diff --git "a/tab\\there.txt" "b/tab\\there.txt"',
    'old mode 100644',
    'new mode 100755',
])


def test_split_patch():
    patches = split_patch(MULTI)
    assert list(patches) == ['mod.py', 'gone.py', 'new name.py', 'img.png', 'tab\there.txt']
    assert patches['gone.py'].split('\n')[-1] == '--- a comment'
    assert patches['mod.py'].split('\n') == MULTI.split('\n')[:7]


def test_patch_paths_rename():
    assert patch_paths(split_patch(MULTI)['new name.py'].split('\n')) == ('old name.py', 'new name.py')
