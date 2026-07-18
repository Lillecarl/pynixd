"""Nix-compatible Ed25519 path signing.

Uses the same key format and fingerprint construction as Nix:
- Keys: ``<name>:<base64>`` (secret key = 32-byte seed, public key = 32 bytes)
- Signatures: ``<name>:<base64>`` (64-byte Ed25519 detached signature)
- Fingerprint: ``1;<store-path>;sha256:<nix32-hash>;<nar-size>;<refs>``
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import nacl.signing
from environs import env

if TYPE_CHECKING:
    from pathlib import Path

    from .serde.aliases import NARHash
    from .serde.valid_path_info import ValidPathInfo


# Nix32 alphabet (kept for reference; encoding is in utils.py)


def get_default_signing_key() -> SecretKey | None:
    """Get the default signing key from PYNIXD_SIGNING_KEY env var."""
    val = env.str("PYNIXD_SIGNING_KEY", "")
    if not val:
        return None
    return SecretKey.from_string(val)


@dataclass
class SecretKey:
    """An Ed25519 secret key in Nix format.

    The key file contains ``<name>:<base64>`` where the base64 decodes to
    a 32-byte Ed25519 seed.
    """

    _signing_key: nacl.signing.SigningKey = field(repr=False)
    name: str = ""

    @classmethod
    def from_file(cls, path: Path) -> SecretKey:
        """Load a secret key from a Nix-format file."""
        text = path.read_text().strip()
        return cls._parse(text)

    @classmethod
    def from_string(cls, text: str) -> SecretKey:
        """Parse a secret key from a Nix-format string."""
        return cls._parse(text.strip())

    @classmethod
    def _parse(cls, text: str) -> SecretKey:
        split = text.split(":", 1)
        name = split[0]
        raw = b64decode(split[1])
        if len(raw) == 64:
            # Full libsodium secret key (seed + public key)
            seed = raw[:32]
        elif len(raw) == 32:
            # Seed-only
            seed = raw
        else:
            raise ValueError(f"Secret key must be 32 or 64 bytes, got {len(raw)}")
        return cls(
            _signing_key=nacl.signing.SigningKey(seed),
            name=name,
        )

    @property
    def public_key_bytes(self) -> bytes:
        """The 32-byte Ed25519 public key."""
        return bytes(self._signing_key.verify_key)

    def sign(self, data: bytes) -> bytes:
        """Sign data, returning a 64-byte Ed25519 signature."""
        return self._signing_key.sign(data).signature

    def sign_fingerprint(self, fingerprint: str) -> str:
        """Sign a fingerprint string, return ``<name>:<base64>`` signature."""
        sig = self.sign(fingerprint.encode("utf-8"))
        return f"{self.name}:{b64encode(sig).decode()}"

    def public_key_string(self) -> str:
        """Return the public key in Nix ``<name>:<base64>`` format."""
        return f"{self.name}:{b64encode(self.public_key_bytes).decode()}"


def fingerprint(
    store_path: object,
    nar_hash: NARHash,
    nar_size: int,
    references: object,
) -> str:
    """Build the Nix fingerprint string that gets signed.

    Format: ``1;<store-path>;sha256:<nix32-hash>;<nar-size>;<comma-separated-refs>``
    """
    refs = ",".join(sorted(str(r) for r in references))  # type: ignore[attr-defined]
    """Build the Nix fingerprint string that gets signed.

    Format: ``1;<store-path>;sha256:<nix32-hash>;<nar-size>;<comma-separated-refs>``
    """
    refs = ",".join(sorted(str(r) for r in references))  # type: ignore[attr-defined]
    return f"1;{store_path};{nar_hash};{nar_size};{refs}"


def sign_path_info(
    key: SecretKey,
    info: ValidPathInfo,
) -> str:
    """Sign a path info and return the signature string.

    Args:
        key: The signing key
        info: The path info to sign

    Returns:
        Signature in ``<name>:<base64>`` format
    """
    fp = fingerprint(
        info.path,
        f"sha256:{info.info.nar_hash}",
        info.info.nar_size,
        info.info.references,
    )
    return key.sign_fingerprint(fp)
