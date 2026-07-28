from scripticus_common.language_compat import LIBRARY_LANGUAGES, language_satisfies


def test_sh_library_serves_every_shell_consumer():
    # The point of the portable baseline: write it once in sh and both halves
    # of the family can source it.
    assert language_satisfies("sh", "sh")
    assert language_satisfies("bash", "sh")


def test_bash_library_serves_only_bash():
    # A bashism sourced into a POSIX sh script is the failure the rule exists
    # to prevent, so this direction must not be symmetric.
    assert language_satisfies("bash", "bash")
    assert not language_satisfies("sh", "bash")


def test_a_non_shell_consumer_can_source_nothing():
    # Nothing outside the shell family sources anything, not even the portable
    # baseline: a python package depending on a shell library is a resolution
    # error, not a warning. D57's rule read literally would allow the sh case;
    # the consumer-side guard is deliberate.
    assert not language_satisfies("python", "bash")
    assert not language_satisfies("powershell", "bash")
    assert not language_satisfies("python", "sh")


def test_library_languages_are_the_shell_family():
    assert LIBRARY_LANGUAGES == ("sh", "bash")
