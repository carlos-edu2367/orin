import threading

import httpx
import pytest

from agentos.oauth.callback_server import CallbackTimeout, LoopbackCallbackServer
from agentos.oauth.flow import OAuthFlowError


def test_redirect_uri_targets_127_0_0_1_on_the_bound_ephemeral_port():
    server = LoopbackCallbackServer(timeout=5)
    try:
        assert server.redirect_uri.startswith("http://127.0.0.1:")
        assert server.redirect_uri.endswith("/callback")
        assert str(server.port) in server.redirect_uri
    finally:
        server.close()


def test_wait_for_code_returns_the_code_from_a_matching_callback():
    server = LoopbackCallbackServer(timeout=5)
    try:
        redirect_uri = server.redirect_uri

        def browser():
            httpx.get(redirect_uri, params={"code": "auth-code-1", "state": "expected-state"})

        threading.Thread(target=browser, daemon=True).start()
        assert server.wait_for_code(expected_state="expected-state") == "auth-code-1"
    finally:
        server.close()


def test_wait_for_code_rejects_a_mismatched_state():
    server = LoopbackCallbackServer(timeout=5)
    try:
        redirect_uri = server.redirect_uri

        def browser():
            httpx.get(redirect_uri, params={"code": "auth-code-1", "state": "attacker-state"})

        threading.Thread(target=browser, daemon=True).start()
        with pytest.raises(OAuthFlowError):
            server.wait_for_code(expected_state="expected-state")
    finally:
        server.close()


def test_wait_for_code_surfaces_a_provider_denial():
    server = LoopbackCallbackServer(timeout=5)
    try:
        redirect_uri = server.redirect_uri

        def browser():
            httpx.get(redirect_uri, params={"error": "access_denied", "state": "expected-state"})

        threading.Thread(target=browser, daemon=True).start()
        with pytest.raises(OAuthFlowError):
            server.wait_for_code(expected_state="expected-state")
    finally:
        server.close()


def test_wait_for_code_times_out_when_no_callback_arrives():
    server = LoopbackCallbackServer(timeout=0.3)
    try:
        with pytest.raises(CallbackTimeout):
            server.wait_for_code(expected_state="expected-state")
    finally:
        server.close()
