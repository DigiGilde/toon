"""Shares the fixtures of test_cli.py with the other test modules."""

from test_cli import (  # noqa: F401
    _fresh_client,
    _no_real_sleep,
    host,
    memory_keyring,
    no_request,
    stub_server,
    token_env,
)
