import hashlib
import os

import pytest

from scripticus_common.treehash import tree_hash


def make_tree(root, files):
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_identical_trees_hash_identically(tmp_path):
    files = {"meta.toml": "x", "src/main.py": "print(1)", "test/keep": ""}
    make_tree(tmp_path / "a", files)
    make_tree(tmp_path / "b", files)
    assert tree_hash(tmp_path / "a") == tree_hash(tmp_path / "b")


def test_hash_has_algorithm_prefix(tmp_path):
    make_tree(tmp_path / "a", {"f": "x"})
    assert tree_hash(tmp_path / "a").startswith("sha256:")


def test_content_change_changes_hash(tmp_path):
    make_tree(tmp_path / "a", {"src/main.py": "print(1)"})
    make_tree(tmp_path / "b", {"src/main.py": "print(2)"})
    assert tree_hash(tmp_path / "a") != tree_hash(tmp_path / "b")


def test_rename_changes_hash(tmp_path):
    make_tree(tmp_path / "a", {"src/main.py": "print(1)"})
    make_tree(tmp_path / "b", {"src/other.py": "print(1)"})
    assert tree_hash(tmp_path / "a") != tree_hash(tmp_path / "b")


def test_moving_file_between_directories_changes_hash(tmp_path):
    make_tree(tmp_path / "a", {"src/main.py": "print(1)"})
    make_tree(tmp_path / "b", {"main.py": "print(1)"})
    assert tree_hash(tmp_path / "a") != tree_hash(tmp_path / "b")


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids newlines in file names")
def test_newline_in_name_cannot_forge_listing_records(tmp_path):
    # A two-file tree, and a one-file tree whose single name is crafted so
    # that a delimiter-based listing would render both trees as identical
    # bytes. The length prefix (D27) must keep their hashes distinct.
    make_tree(tmp_path / "a", {"a": "X", "b": "Y"})
    y_hash = hashlib.sha256(b"Y").hexdigest()
    make_tree(tmp_path / "b", {f"a\nblob {y_hash} 1:b": "X"})
    assert tree_hash(tmp_path / "a") != tree_hash(tmp_path / "b")


@pytest.mark.skipif(os.name == "nt", reason="no executable bit on Windows")
def test_executable_bit_does_not_affect_hash(tmp_path):
    # Deliberate: zip extraction drops the executable bit, and identity must
    # be the same whichever archive container the content travelled in.
    files = {"src/main.sh": "echo hi"}
    make_tree(tmp_path / "a", files)
    make_tree(tmp_path / "b", files)
    (tmp_path / "a" / "src" / "main.sh").chmod(0o755)
    assert tree_hash(tmp_path / "a") == tree_hash(tmp_path / "b")
