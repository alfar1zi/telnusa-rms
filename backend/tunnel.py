"""Server-side SSH Tunnel Manager for Teltonika RUT906 native WebUI access."""
from __future__ import annotations

import os
import socket
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .config import config
from .remote import DeviceRemoteTarget, resolve_device_target
from .store import Store


class TunnelState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    FAILED = "FAILED"
    STOPPING = "STOPPING"
    BLOCKED_CONFIG = "BLOCKED_CONFIG"


class TunnelResourceExhausted(RuntimeError):
    """Raised when a bounded tunnel resource (loopback port, concurrency slot) is exhausted.

    Explicit and recoverable: the caller may free a slot and retry. It is never
    silently absorbed into an unbounded fallback.
    """


@dataclass
class TunnelInfo:
    device_id: str
    local_port: int | None = None
    pid: int | None = None
    state: TunnelState = TunnelState.STOPPED
    started_at: int | None = None
    last_used: int | None = None
    last_health_check: int | None = None
    failure_count: int = 0
    error: str | None = None
    target: DeviceRemoteTarget | None = None

    def to_dict(self) -> dict:
        return {
            "deviceId": self.device_id,
            "localPort": self.local_port,
            "pid": self.pid,
            "state": self.state.value,
            "startedAt": self.started_at,
            "lastUsed": self.last_used,
            "lastHealthCheck": self.last_health_check,
            "failureCount": self.failure_count,
            "error": self.error,
        }


def now_ms() -> int:
    return int(time.time() * 1000)


def validate_ssh_config(key_path: str, known_hosts_path: str) -> tuple[bool, str | None]:
    """Validate server-side SSH key and known_hosts configuration.

    Rules:
    - Never accept empty paths.
    - Files must exist and be regular files.
    - Files must be readable.
    - SSH private key on POSIX must have strict permissions (0600 or stricter, no group/world access).
    """
    if not key_path or not key_path.strip():
        return False, "SSH private key path not configured (NMS_RUT_SSH_KEY_PATH)."

    if not known_hosts_path or not known_hosts_path.strip():
        return False, "SSH known_hosts path not configured (NMS_RUT_KNOWN_HOSTS_PATH)."

    clean_key = key_path.strip()
    if not os.path.exists(clean_key) or not os.path.isfile(clean_key):
        return False, f"SSH private key file not found: {clean_key}"

    if not os.access(clean_key, os.R_OK):
        return False, f"SSH private key file not readable: {clean_key}"

    # Strict permission check on POSIX systems (mode must not have group/other r/w/x permissions)
    if os.name == "posix":
        try:
            st = os.stat(clean_key)
            if st.st_mode & 0o077 != 0:
                mode_oct = oct(st.st_mode & 0o777)
                return False, f"SSH private key has insecure permissions ({mode_oct}, must be 0600 or stricter): {clean_key}"
        except OSError as exc:
            return False, f"Failed to check SSH private key permissions: {exc}"

    clean_hosts = known_hosts_path.strip()
    if not os.path.exists(clean_hosts) or not os.path.isfile(clean_hosts):
        return False, f"SSH known_hosts file not found: {clean_hosts}"

    if not os.access(clean_hosts, os.R_OK):
        return False, f"SSH known_hosts file not readable: {clean_hosts}"

    return True, None


# Accounts that remote access must never default to. Remote management of the device
# is performed by a named, authorized, least-privilege account.
FORBIDDEN_SSH_USERS = frozenset({"root"})


def validate_ssh_user(ssh_user: str | None) -> tuple[bool, str | None]:
    """Validate the configured SSH identity for remote access.

    REMOTE-03: an unset identity fails closed. There is deliberately no default,
    and a privileged account is rejected outright rather than silently downgraded.
    """
    if not ssh_user or not str(ssh_user).strip():
        return False, "SSH user not configured (NMS_RUT_SSH_USER)."

    clean = str(ssh_user).strip()
    if clean in FORBIDDEN_SSH_USERS:
        return False, (
            f"SSH user {clean!r} is not permitted for remote access. "
            "Configure a named, authorized, non-root account (NMS_RUT_SSH_USER)."
        )
    # An SSH user is a bare account name; '@' starts user@host and whitespace would
    # split the argument. Reject both rather than letting them reach the argv.
    if "@" in clean or any(ch.isspace() for ch in clean):
        return False, f"SSH user {clean!r} is not a bare account name."
    if clean.startswith("-"):
        return False, f"SSH user {clean!r} must not start with '-'."
    return True, None



def allocate_loopback_port(used_ports: set[int], start_port: int = 18000, end_port: int = 19000) -> int:
    """Allocate a local TCP port bound strictly to 127.0.0.1 from a bounded range.

    REMOTE-02: the range is exclusive and exhaustion is an explicit, recoverable
    failure. There is no fallback to an OS-assigned ephemeral port, which would
    silently escape the configured range and make the proxy target unpredictable.
    """
    if not isinstance(used_ports, set):
        raise TypeError("used_ports must be a set")
    for port in range(start_port, end_port):
        if port in used_ports:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue

    raise TunnelResourceExhausted(
        f"No free loopback port in range {start_port}-{end_port} "
        f"({len(used_ports)} already allocated)."
    )


def build_ssh_command(
    target: DeviceRemoteTarget,
    local_port: int,
    key_path: str,
    known_hosts_path: str,
    ssh_user: str,
    # REMOTE-03: ssh_user is required and must be a validated non-root account.
    # There is intentionally no default identity.
) -> list[str]:
    """Construct deterministic SSH port forwarding argument list.

    Security invariants:
    - Returns a list of strings (never a shell string).
    - Local forwarding binds strictly to 127.0.0.1.
    - BatchMode=yes (no interactive password prompt).
    - StrictHostKeyChecking=yes (requires server-side known_hosts).
    - ExitOnForwardFailure=yes.
    - No credentials or passwords in arguments.
    """
    if not isinstance(local_port, int) or local_port <= 0 or local_port > 65535:
        raise ValueError(f"Invalid local_port: {local_port}")

    # REMOTE-03: fail closed before anything reaches argv.
    user_valid, user_err = validate_ssh_user(ssh_user)
    if not user_valid:
        raise ValueError(user_err)

    return [
        "ssh",
        "-N",
        "-T",
        "-oBatchMode=yes",
        "-oExitOnForwardFailure=yes",
        "-oStrictHostKeyChecking=yes",
        "-oServerAliveInterval=15",
        "-oServerAliveCountMax=3",
        f"-oUserKnownHostsFile={known_hosts_path}",
        "-i",
        key_path,
        "-L",
        f"127.0.0.1:{local_port}:{target.webui_target_host}:{target.webui_target_port}",
        "-p",
        str(target.ssh_port),
        f"{ssh_user}@{target.ssh_host}",
    ]


def default_socket_checker(host: str, port: int, timeout: float = 0.2) -> bool:
    """Check if TCP listener on host:port is responding."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def default_process_runner(cmd: list[str]) -> subprocess.Popen:
    """Default Popen process runner with shell=False explicitly."""
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
    )


class TunnelManager:
    """Thread-safe Lifecycle Manager for RUT906 SSH WebUI Tunnels."""

    def __init__(
        self,
        store: Store | None = None,
        key_path: str | None = None,
        known_hosts_path: str | None = None,
        ssh_user: str | None = None,
        idle_timeout: int | None = None,
        max_failures: int | None = None,
        process_runner: Callable[[list[str]], Any] | None = None,
        socket_checker: Callable[[str, int, float], bool] | None = None,
        max_concurrent: int | None = None,
        port_range_start: int | None = None,
        port_range_end: int | None = None,
        startup_wait_seconds: float | None = None,
        lock_cache_max: int | None = None,
    ):
        self.store = store
        self.key_path = key_path if key_path is not None else config.RUT_SSH_KEY_PATH
        self.known_hosts_path = known_hosts_path if known_hosts_path is not None else config.RUT_KNOWN_HOSTS_PATH
        # REMOTE-03: never falls back to a privileged identity.
        self.ssh_user = (ssh_user if ssh_user is not None else config.RUT_SSH_USER or "").strip()
        self.idle_timeout_seconds = idle_timeout if idle_timeout is not None else config.RUT_TUNNEL_IDLE_TIMEOUT_SECONDS
        self.max_failures = max_failures if max_failures is not None else config.RUT_TUNNEL_MAX_FAILURES
        self.process_runner = process_runner or default_process_runner
        self.socket_checker = socket_checker or default_socket_checker
        self.startup_wait_seconds = startup_wait_seconds if startup_wait_seconds is not None else config.RUT_TUNNEL_STARTUP_WAIT_SECONDS

        # REMOTE-02: explicit, configured resource bounds.
        self.max_concurrent = max_concurrent if max_concurrent is not None else config.RUT_TUNNEL_MAX_CONCURRENT
        self.port_range_start = port_range_start if port_range_start is not None else config.RUT_TUNNEL_PORT_RANGE_START
        self.port_range_end = port_range_end if port_range_end is not None else config.RUT_TUNNEL_PORT_RANGE_END
        self.lock_cache_max = lock_cache_max if lock_cache_max is not None else config.RUT_TUNNEL_LOCK_CACHE_MAX

        self._lock = threading.RLock()
        self._device_locks: dict[str, threading.RLock] = {}
        self._tunnels: dict[str, TunnelInfo] = {}
        self._processes: dict[str, Any] = {}
        self._used_ports: set[int] = set()

    def _evict_device_locks(self, keep: str | None = None) -> None:
        """Bound the per-device lock cache. Caller must hold self._lock.

        A lock is evictable only when it is idle and its device holds no live tunnel,
        so eviction can never race an in-flight operation. `keep` protects the lock
        the caller is about to return.
        """
        if len(self._device_locks) <= self.lock_cache_max:
            return
        live = {d for d, t in self._tunnels.items() if t.state != TunnelState.STOPPED}
        # Acquire (non-blocking) to prove the lock is idle, and hold it until it has
        # been dropped from the cache so eviction cannot race an in-flight caller.
        held: list[str] = []
        for d, lk in self._device_locks.items():
            if d in live or d == keep:
                continue
            if lk.acquire(blocking=False):
                held.append(d)

        # Evict in a deterministic order so repeated runs allocate the same way.
        overflow = len(self._device_locks) - self.lock_cache_max
        for d in sorted(held)[:overflow]:
            lk = self._device_locks.pop(d)
            lk.release()
        for d in sorted(held)[overflow:]:
            self._device_locks[d].release()

    def _get_device_lock(self, device_id: str) -> threading.RLock:
        with self._lock:
            if device_id not in self._device_locks:
                self._device_locks[device_id] = threading.RLock()
                self._evict_device_locks(keep=device_id)
            return self._device_locks[device_id]

    def status(self, device_id: str) -> TunnelInfo:
        """Get current tunnel status for device_id."""
        with self._lock:
            info = self._tunnels.get(device_id)
            if not info:
                return TunnelInfo(device_id=device_id, state=TunnelState.STOPPED)
            return TunnelInfo(
                device_id=info.device_id,
                local_port=info.local_port,
                pid=info.pid,
                state=info.state,
                started_at=info.started_at,
                last_used=info.last_used,
                last_health_check=info.last_health_check,
                failure_count=info.failure_count,
                error=info.error,
                target=info.target,
            )

    def health_check(self, device_id: str) -> bool:
        """Verify process is alive and 127.0.0.1:<local_port> is listening."""
        with self._lock:
            info = self._tunnels.get(device_id)
            proc = self._processes.get(device_id)
            if not info or info.state != TunnelState.READY or not info.local_port or not proc:
                return False

            # Check if subprocess is still alive
            if hasattr(proc, "poll") and proc.poll() is not None:
                info.state = TunnelState.FAILED
                info.error = f"SSH tunnel process exited with code {proc.poll()}"
                info.pid = None
                return False

            # Check if socket listener is accepting connections
            listening = self.socket_checker("127.0.0.1", info.local_port, 0.2)
            info.last_health_check = now_ms()
            if not listening:
                info.state = TunnelState.FAILED
                info.error = f"Port 127.0.0.1:{info.local_port} stopped listening."
                return False

            return True

    def ensure(self, device_id: str) -> TunnelInfo:
        """Ensure an active, healthy tunnel exists for the trusted device."""
        dev_lock = self._get_device_lock(device_id)
        with dev_lock:
            # 1. Resolve trusted target
            target = resolve_device_target(device_id, self.store)
            if not target:
                return TunnelInfo(
                    device_id=device_id,
                    state=TunnelState.FAILED,
                    error=f"Device not found in trusted registry: {device_id}",
                )

            # 2. Check existing tunnel
            with self._lock:
                current = self._tunnels.get(device_id)
                if current and current.state == TunnelState.READY and current.local_port:
                    if self.health_check(device_id):
                        current.last_used = now_ms()
                        return current

            # 3. Check failure limit
            with self._lock:
                current = self._tunnels.get(device_id)
                if current and current.failure_count >= self.max_failures:
                    return TunnelInfo(
                        device_id=device_id,
                        state=TunnelState.FAILED,
                        error=f"Max connection failures ({self.max_failures}) reached. Manual reset or timeout required.",
                        failure_count=current.failure_count,
                    )

            # 4. Start tunnel
            return self._start_locked(target)

    def start(self, device_id: str) -> TunnelInfo:
        """Explicitly start tunnel for device_id."""
        dev_lock = self._get_device_lock(device_id)
        with dev_lock:
            target = resolve_device_target(device_id, self.store)
            if not target:
                return TunnelInfo(
                    device_id=device_id,
                    state=TunnelState.FAILED,
                    error=f"Device not found in trusted registry: {device_id}",
                )
            return self._start_locked(target)

    def _start_locked(self, target: DeviceRemoteTarget) -> TunnelInfo:
        device_id = target.device_id

        # Validate the configured SSH identity (REMOTE-03). An unset or privileged
        # account is BLOCKED_CONFIG, never silently defaulted.
        user_valid, user_err = validate_ssh_user(self.ssh_user)
        if not user_valid:
            with self._lock:
                info = TunnelInfo(
                    device_id=device_id,
                    state=TunnelState.BLOCKED_CONFIG,
                    error=user_err,
                    target=target,
                )
                self._tunnels[device_id] = info
                return info

        # Validate SSH configuration
        valid, err = validate_ssh_config(self.key_path, self.known_hosts_path)
        if not valid:
            with self._lock:
                info = TunnelInfo(
                    device_id=device_id,
                    state=TunnelState.BLOCKED_CONFIG,
                    error=err,
                    target=target,
                )
                self._tunnels[device_id] = info
                return info

        # Cleanup existing dead process if any
        self._stop_internal(device_id)

        # Allocate dynamic loopback port within the configured bounded range
        with self._lock:
            active = sum(
                1 for d, t in self._tunnels.items()
                if t.state != TunnelState.STOPPED and d != device_id
            )
            if active >= self.max_concurrent:
                info = TunnelInfo(
                    device_id=device_id,
                    state=TunnelState.FAILED,
                    error=(
                        f"Concurrent tunnel limit reached ({self.max_concurrent} active). "
 "Retry after an idle tunnel is reaped."
                    ),
                    target=target,
                )
                self._tunnels[device_id] = info
                return info

            try:
                local_port = allocate_loopback_port(
                    self._used_ports,
                    start_port=self.port_range_start,
                    end_port=self.port_range_end,
                )
            except TunnelResourceExhausted as exc:
                info = TunnelInfo(
                    device_id=device_id,
                    state=TunnelState.FAILED,
                    error=str(exc),
                    target=target,
                )
                self._tunnels[device_id] = info
                return info
            self._used_ports.add(local_port)

            info = TunnelInfo(
                device_id=device_id,
                local_port=local_port,
                state=TunnelState.STARTING,
                target=target,
                failure_count=self._tunnels.get(device_id, TunnelInfo(device_id)).failure_count,
            )
            self._tunnels[device_id] = info

        # Build command
        cmd = build_ssh_command(
            target=target,
            local_port=local_port,
            key_path=self.key_path,
            known_hosts_path=self.known_hosts_path,
            ssh_user=self.ssh_user,
        )

        # Spawn process
        try:
            proc = self.process_runner(cmd)
            with self._lock:
                self._processes[device_id] = proc
                info.pid = getattr(proc, "pid", None)
        except Exception as exc:
            with self._lock:
                self._used_ports.discard(local_port)
                info.state = TunnelState.FAILED
                info.error = f"Failed to spawn SSH process: {exc}"
                info.failure_count += 1
                return info

        # Wait for listener or early exit
        time.sleep(self.startup_wait_seconds)

        # Check process status
        if hasattr(proc, "poll") and proc.poll() is not None:
            with self._lock:
                self._used_ports.discard(local_port)
                info.state = TunnelState.FAILED
                info.error = f"SSH tunnel exited immediately with code {proc.poll()}"
                info.failure_count += 1
                info.pid = None
                self._processes.pop(device_id, None)
                return info

        # Check listener
        is_ready = self.socket_checker("127.0.0.1", local_port, 0.5)
        with self._lock:
            if is_ready:
                info.state = TunnelState.READY
                info.started_at = now_ms()
                info.last_used = now_ms()
                info.last_health_check = now_ms()
                info.failure_count = 0
                info.error = None
            else:
                info.state = TunnelState.FAILED
                info.error = f"SSH tunnel started but 127.0.0.1:{local_port} is not listening."
                info.failure_count += 1
                self._stop_internal(device_id)

            return info

    def stop(self, device_id: str) -> TunnelInfo:
        """Explicitly stop and release tunnel for device_id."""
        dev_lock = self._get_device_lock(device_id)
        with dev_lock:
            with self._lock:
                return self._stop_internal(device_id)

    def _stop_internal(self, device_id: str) -> TunnelInfo:
        proc = self._processes.pop(device_id, None)
        info = self._tunnels.get(device_id, TunnelInfo(device_id=device_id))

        if info.local_port:
            self._used_ports.discard(info.local_port)

        if proc:
            try:
                if hasattr(proc, "terminate"):
                    proc.terminate()
                    if hasattr(proc, "wait"):
                        try:
                            proc.wait(timeout=1.0)
                        except Exception:
                            if hasattr(proc, "kill"):
                                proc.kill()
            except Exception:
                pass

        info.state = TunnelState.STOPPED
        info.pid = None
        info.local_port = None
        self._tunnels[device_id] = info
        return info

    def reap_dead_processes(self) -> None:
        """Scan active tunnels and clean up any processes that exited."""
        with self._lock:
            for device_id, proc in list(self._processes.items()):
                if hasattr(proc, "poll") and proc.poll() is not None:
                    info = self._tunnels.get(device_id)
                    if info:
                        if info.local_port:
                            self._used_ports.discard(info.local_port)
                        info.state = TunnelState.FAILED
                        info.error = f"Process exited with code {proc.poll()}"
                        info.pid = None
                    self._processes.pop(device_id, None)

    def cleanup_idle(self, timeout_seconds: int | None = None) -> list[str]:
        """Stop tunnels that have been idle longer than timeout_seconds."""
        ttl = timeout_seconds if timeout_seconds is not None else self.idle_timeout_seconds
        now = now_ms()
        stopped = []

        with self._lock:
            for device_id, info in list(self._tunnels.items()):
                if info.state == TunnelState.READY and info.last_used:
                    idle_ms = now - info.last_used
                    if idle_ms >= ttl * 1000:
                        self._stop_internal(device_id)
                        stopped.append(device_id)

        return stopped

    def touch(self, device_id: str) -> None:
        """Update last_used timestamp for an active tunnel."""
        with self._lock:
            info = self._tunnels.get(device_id)
            if info and info.state == TunnelState.READY:
                info.last_used = now_ms()

    def cleanup_all(self) -> None:
        """Stop all running tunnels on shutdown."""
        with self._lock:
            for device_id in list(self._tunnels.keys()):
                self._stop_internal(device_id)


class TunnelMaintenanceWorker:
    """Periodic maintenance runner for tunnel reaping and idle cleanup."""

    def __init__(self, manager: TunnelManager, interval: float = 10.0):
        self.manager = manager
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tunnel-maintenance", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.run_once()

    def run_once(self) -> None:
        try:
            self.manager.reap_dead_processes()
            self.manager.cleanup_idle()
        except Exception:
            pass


# Global singleton instance
tunnel_manager = TunnelManager()
