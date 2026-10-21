"""Auth primitives. New hashes use Argon2id exclusively; PBKDF2 legacy verifier supported for migration."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque

from .config import config

try:
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import Argon2Error, VerifyMismatchError
    _ARGON2 = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, type=Type.ID)
except ImportError:  # pragma: no cover
    _ARGON2 = None
    Argon2Error = VerifyMismatchError = Exception

ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def hash_password(password: str) -> str:
    """Hash password using Argon2id. Raises RuntimeError if Argon2id is unavailable."""
    if not _ARGON2:
        raise RuntimeError("ARGON2ID_REQUIRED: argon2-cffi is not installed. Production requires Argon2id password hashing.")
    material = config.AUTH_PEPPER + password
    return _ARGON2.hash(material)


def verify_password(password: str, stored: str | None) -> bool:
    """Verify password against Argon2id or legacy PBKDF2 hash."""
    if not stored:
        return False
    material = config.AUTH_PEPPER + password
    if stored.startswith("$argon2"):
        if not _ARGON2:
            return False
        try:
            return _ARGON2.verify(stored, material)
        except (VerifyMismatchError, Argon2Error, TypeError):
            return False
    try:
        parts = stored.split("$")
        if len(parts) == 6 and parts[1] == "pbkdf2" and parts[2] == "sha256":
            _, _, _, iters, salt_hex, dk_hex = parts
            dk = hashlib.pbkdf2_hmac("sha256", material.encode(), bytes.fromhex(salt_hex), int(iters))
            return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False
    return False


def verify_password_allowing_legacy(password: str, stored: str | None) -> tuple[bool, bool]:
    """Verify, tolerating a hash created before a pepper was configured.

    Returns (ok, needs_upgrade). With no pepper configured this is exactly
    verify_password(). With a pepper set, a row written before the pepper still
    verifies once and the caller is told to store the peppered form: enabling the
    pepper then hardens the table as users log in, instead of locking everyone out
    the moment the value appears in the environment.
    """
    if verify_password(password, stored):
        return True, False
    if not config.AUTH_PEPPER or not stored:
        return False, False
    text = str(stored)
    if text.startswith("$argon2"):
        if not _ARGON2:
            return False, False
        try:
            if _ARGON2.verify(text, password):
                return True, True
        except (VerifyMismatchError, Argon2Error, TypeError):
            return False, False
        return False, False
    try:
        parts = text.split("$")
        if len(parts) == 6 and parts[1] == "pbkdf2" and parts[2] == "sha256":
            _, _, _, iters, salt_hex, dk_hex = parts
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
            if hmac.compare_digest(dk.hex(), dk_hex):
                return True, True
    except (ValueError, TypeError):
        return False, False
    return False, False

def validate_password_strength(password: object) -> tuple[bool, str]:
    """Validate password format and minimum requirements."""
    if not isinstance(password, str):
        return False, "Password must be a string."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, f"Password cannot exceed {MAX_PASSWORD_LENGTH} characters."
    return True, ""


_dummy_hash: str | None = None

def dummy_password_hash() -> str | None:
    """A hash of an unguessable throwaway password.

    Login verifies against this when the account does not exist or has no local
    hash, so response time cannot reveal which email addresses are real.
    """
    global _dummy_hash
    if _dummy_hash is None:
        try:
            _dummy_hash = hash_password(secrets.token_urlsafe(32))
        except RuntimeError:
            return None
    return _dummy_hash

def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


class RateLimiter:
    """Bounded sliding-window limiter: 'max/per_seconds' per key. Memory-safe."""

    def __init__(self, spec: str = "10/60", max_keys: int = 10_000):
        try:
            max_hits, window = spec.split("/")
            self.max_hits = int(max_hits)
            self.window = float(window)
        except ValueError:
            self.max_hits, self.window = 10, 60.0
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def _evict_expired(self, now: float) -> None:
        """Remove expired keys to prevent memory growth."""
        expired = [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]
        for k in expired:
            del self._hits[k]
        while len(self._hits) >= self.max_keys:
            self._hits.popitem(last=False)
    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if key in self._hits:
                self._hits.move_to_end(key)
                q = self._hits[key]
            else:
                if len(self._hits) >= self.max_keys:
                    self._evict_expired(now)
                q = self._hits.setdefault(key, deque())

            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_hits:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._hits.get(key, deque())
            if not q:
                return 0
            return max(0, int(self.window - (time.monotonic() - q[0])))


def valid_email(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= 320
            and "@" in value and "." in value.rsplit("@", 1)[-1])


def safe_role(value: object) -> str | None:
    return value if value in ("admin", "operator", "viewer") else None
