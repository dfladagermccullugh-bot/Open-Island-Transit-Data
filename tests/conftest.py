import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="rewrite tests/golden/ from the current code instead of comparing with it",
    )


@pytest.fixture
def update_golden(request):
    return request.config.getoption("--update-golden")
