import pytest

from agentos.accounts.errors import LoginLocked, WeakPassword
from agentos.accounts.passwords import (
    MAX_PASSWORD_LENGTH, burn_password_check, generate_temporary_password, hash_password,
    validate_password, verify_password,
)


def test_hash_round_trip_and_wrong_password():
    encoded = hash_password("correct horse battery")
    assert encoded.startswith("scrypt$32768$8$1$")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("correct horse batterY", encoded)


def test_each_hash_has_its_own_salt():
    assert hash_password("same password!") != hash_password("same password!")


def test_parameters_are_read_from_the_stored_hash():
    # A hash written with lighter parameters keeps verifying after the default is raised.
    from agentos.accounts import passwords
    light = passwords._hash_with("legacy-password", n=2**14, r=8, p=1)
    assert light.startswith("scrypt$16384$8$1$")
    assert verify_password("legacy-password", light)


@pytest.mark.parametrize("encoded", ["", "bcrypt$x", "scrypt$1$2$3", "scrypt$abc$8$1$AA$AA", "scrypt$32768$8$1$***$***"])
def test_malformed_hash_never_raises(encoded):
    assert verify_password("anything at all", encoded) is False


@pytest.mark.parametrize("password", ["short", "x" * 9, "y" * (MAX_PASSWORD_LENGTH + 1)])
def test_password_length_is_bounded(password):
    with pytest.raises(WeakPassword):
        validate_password(password)


def test_valid_password_passes_validation():
    validate_password("ten chars!")


def test_temporary_password_is_strong_enough_to_pass_validation():
    password = generate_temporary_password()
    assert len(password) == 16
    validate_password(password)
    assert generate_temporary_password() != password


def test_burn_check_runs_without_error():
    burn_password_check("whatever")


def test_login_locked_carries_retry_after():
    error = LoginLocked(retry_after=900)
    assert (error.status, error.code, error.retryable, error.retry_after) == (429, "login_locked", True, 900)
