import pytest
from pydantic import ValidationError

from scripticus_schema.yank_api import YankRequest, YankResult


def test_yank_request_carries_the_desired_flag_state():
    assert YankRequest(yanked=True).yanked is True
    assert YankRequest(yanked=False).yanked is False


def test_yank_request_requires_an_explicit_state():
    # No default: the client always says whether it means to yank or un-yank.
    with pytest.raises(ValidationError):
        YankRequest()


def test_yank_result_round_trips():
    result = YankResult(
        namespace="infra", name="backup-rotate", version="1.2.0", yanked=True
    )
    assert result.model_dump() == {
        "namespace": "infra",
        "name": "backup-rotate",
        "version": "1.2.0",
        "yanked": True,
    }
