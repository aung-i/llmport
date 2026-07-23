"""Tests for config crypto module."""

from llmport.config.crypto import generate_key, encrypt, decrypt


def test_encrypt_decrypt_roundtrip():
    key = generate_key()
    plaintext = "sk-ant-api03-secret-key"
    ciphertext = encrypt(key, plaintext)
    assert isinstance(ciphertext, bytes)
    assert ciphertext != plaintext.encode()
    result = decrypt(key, ciphertext)
    assert result == plaintext


def test_generate_key_is_unique():
    k1 = generate_key()
    k2 = generate_key()
    assert k1 != k2


def test_wrong_key_fails():
    k1 = generate_key()
    k2 = generate_key()
    ciphertext = encrypt(k1, "secret")
    try:
        decrypt(k2, ciphertext)
        assert False, "Should have raised"
    except Exception:
        pass
