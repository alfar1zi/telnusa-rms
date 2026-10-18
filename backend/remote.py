"""Server-side trusted device remote target resolver."""
from __future__ import annotations

from dataclasses import dataclass
from .config import config
from .store import Store


@dataclass(frozen=True)
class DeviceRemoteTarget:
    device_id: str
    ssh_host: str
    ssh_port: int
    webui_target_host: str
    webui_target_port: int


# Remote access is granted only to devices that exist in the device registry.
# The registry proves identity; the server-side configuration supplies the network
# target. A host/port is never taken from the request, the device_id, or a constant.
#
# Devices are only remotely reachable once they are `integration_status = accepted`,
# which is the same registry verdict that gates the rest of the integration.
REMOTE_CAPABLE_INTEGRATION_STATUS = "accepted"


def _device_is_remotely_reachable(dev: dict | None) -> bool:
    if not dev:
        return False
    if not str(dev.get("device_id") or "").strip():
        return False
    return dev.get("integration_status") == REMOTE_CAPABLE_INTEGRATION_STATUS


def resolve_device_target(device_id: str, store: Store | None = None) -> DeviceRemoteTarget | None:
    """Accept ONLY device_id and return a trusted server-side target mapping.

    Never accept arbitrary host/url/target from request parameters or client payloads.

    Resolution is registry-only. An unknown device, or a registered device that is not
    integration-accepted, has no remote target: this function returns None rather than
    falling back to any default, hardcoded, or test identity.
    """
    if not isinstance(device_id, str) or not device_id.strip():
        return None
    if store is None:
        # Without the registry there is no trustworthy identity to authorize.
        return None

    clean_id = device_id.strip()
    dev = store.get_device(clean_id)
    if not _device_is_remotely_reachable(dev):
        return None

    return DeviceRemoteTarget(
        device_id=clean_id,
        ssh_host=config.RUT_SSH_HOST,
        ssh_port=config.RUT_SSH_PORT,
        webui_target_host=config.RUT_WEBUI_TARGET_HOST,
        webui_target_port=config.RUT_WEBUI_TARGET_PORT,
    )
