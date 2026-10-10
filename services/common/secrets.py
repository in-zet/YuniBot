from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class DecryptionError(ValueError):
    pass


class SecretsService:
    """clal 쿠키 같은 비밀값의 암호화/복호화. 복호화한 평문은 반환값으로만 나가고, 이
    클래스는 그 값을 로그로도, 예외 메시지로도 남기지 않는다 — 호출하는 쪽(4단계
    chunithm_net 서비스)도 같은 규칙을 지켜야 비밀값이 스킬/로그/LLM 입력으로 새지 않는다."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise DecryptionError("복호화할 수 없다 — 키가 바뀌었거나 값이 손상됐다") from exc
