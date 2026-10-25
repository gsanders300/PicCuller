"""Keep every test off the network.

The startup model-update check is on by default, so any test that reaches `run_pipeline`
would contact Hugging Face and GitHub. These fixtures replace the check with a canned
result and make a stray `requests` call fail loudly. A test that needs a different
result patches over them.
"""

from unittest.mock import patch

import pytest

from model_updates import ModelUpdateCheck


@pytest.fixture(autouse=True)
def no_network():
    def forbidden(*args, **kwargs):
        raise AssertionError("A test tried to use the network")

    with (
        patch("model_updates.check_model_updates", return_value=ModelUpdateCheck((), {})),
        patch("requests.get", forbidden),
        patch("requests.head", forbidden),
    ):
        yield
