import logging

import pytest
from cryptography.fernet import Fernet

from services.common.secrets import DecryptionError, SecretsService


def test_round_trip() -> None:
    service = SecretsService(Fernet.generate_key())

    ciphertext = service.encrypt("clal=super-secret-cookie")

    assert ciphertext != b"super-secret-cookie"
    assert service.decrypt(ciphertext) == "clal=super-secret-cookie"


def test_wrong_key_fails_to_decrypt() -> None:
    service_a = SecretsService(Fernet.generate_key())
    service_b = SecretsService(Fernet.generate_key())

    ciphertext = service_a.encrypt("secret")

    try:
        service_b.decrypt(ciphertext)
        raise AssertionError("다른 키로 복호화가 성공하면 안 된다")
    except DecryptionError:
        pass


def test_decrypted_value_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    service = SecretsService(Fernet.generate_key())
    ciphertext = service.encrypt("clal=super-secret-cookie")

    with caplog.at_level(logging.DEBUG):
        plaintext = service.decrypt(ciphertext)

    assert plaintext == "clal=super-secret-cookie"
    assert "super-secret-cookie" not in caplog.text
