"""Fernet-based encryption for API keys and sensitive config."""

from cryptography.fernet import Fernet


def generate_key() -> bytes:
    """Generate a new Fernet key."""
    return Fernet.generate_key()


def encrypt(key: bytes, plaintext: str) -> bytes:
    """Encrypt a plaintext string with the given Fernet key."""
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt(key: bytes, ciphertext: bytes) -> str:
    """Decrypt ciphertext bytes back to a string with the given Fernet key."""
    f = Fernet(key)
    return f.decrypt(ciphertext).decode("utf-8")
