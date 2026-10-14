import { useEffect, useState } from "react";
import { getDevice } from "@/services/api";
import { siteLabel } from "@/lib/deviceSemantics";

/**
 * The operator-facing name of a device: its site label, never the serial-shaped
 * technical id. Pages that only carry a device id in the URL use this so a heading
 * reads "RUT-TEST-001" rather than "RUT-1234567890"; the id stays available for the
 * secondary line where unambiguous identification is needed.
 *
 * Unresolved (loading, unknown device, fetch failure) returns an empty string so the
 * caller falls back to its own placeholder instead of showing a wrong name.
 */
export function useDeviceLabel(deviceId: string | undefined): string {
  const [label, setLabel] = useState("");
  useEffect(() => {
    if (!deviceId) {
      setLabel("");
      return;
    }
    let live = true;
    getDevice(deviceId)
      .then((dev) => {
        if (live && dev) setLabel(siteLabel(dev.siteName, dev.siteId));
      })
      .catch(() => {
        // Keep the previous label: an unreachable registry must not relabel a device.
      });
    return () => {
      live = false;
    };
  }, [deviceId]);
  return label;
}
