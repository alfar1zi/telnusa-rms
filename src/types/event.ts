/**
 *  the canonical event contract, shared with the backend
 * (`canonical_event_type` in backend/zabbix_client.py).
 *
 * `status_change`, `config_change` and `reboot` are part of the contract but
 * are not produced by the Zabbix event source today. They stay declared so a
 * future source can emit them without another contract change, but not so the UI
 * can infer them. Anything the backend cannot classify arrives as `unknown`.
 */
export type EventType =
  | "alarm_created"
  | "alarm_resolved"
  | "status_change"
  | "config_change"
  | "reboot"
  | "unknown";

export interface DeviceEvent {
  id: string;
  deviceId: string;
  type: EventType;
  message: string;
  timestamp: number;
  // Emitted by the backend but not previously declared here. Optional so mock
  // and older payloads stay valid.
  severity?: string;
  siteId?: string;
  siteName?: string | null;
}
