import base64
import hashlib

from agentos.oauth.pkce import code_challenge_for, generate_code_verifier, generate_state


def test_code_verifier_is_high_entropy_and_url_safe():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert all(char.isalnum() or char in "-._~" for char in verifier)
    assert generate_code_verifier() != verifier


def test_code_challenge_is_the_s256_transform_of_the_verifier():
    verifier = generate_code_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert code_challenge_for(verifier) == expected


def test_state_is_unique_and_unguessable():
    a, b = generate_state(), generate_state()
    assert a != b
    assert len(a) >= 32
