from hashlib import sha256


def sha256_hex(content: bytes) -> str:
    return sha256(content).hexdigest()
