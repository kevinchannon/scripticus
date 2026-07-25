from scripticus_common.snippet_variants import (
    variant_path,
    variants_from_paths,
    variant_of,
)


def test_flat_src_file_is_a_variant():
    assert variant_of("src/args.sh") == ("args", "sh")


def test_only_flat_src_files_count():
    # A README, a nested tree, and an extension-less file are all package
    # content, not snippet code — mistaking any of them for a variant would
    # invent snippet names nobody declared.
    assert variant_of("README.md") is None
    assert variant_of("src/vendor/args.sh") is None
    assert variant_of("src/LICENSE") is None
    assert variant_of("meta.toml") is None


def test_windows_separators_derive_the_same_variant():
    # The rule must agree on both sides of the wire (D51), and a zip built on
    # Windows can carry backslashes.
    assert variant_of("src\\args.sh") == ("args", "sh")


def test_same_name_in_many_languages_is_one_snippet():
    variants = variants_from_paths(
        ["src/args.py", "src/args.cpp", "src/args.sh", "src/trap.sh", "README.md"]
    )
    assert variants == {"args": ["cpp", "py", "sh"], "trap": ["sh"]}


def test_variant_path_round_trips():
    assert variant_of(variant_path("args", "cpp")) == ("args", "cpp")
