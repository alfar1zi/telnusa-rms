/**
 *  single authority for event type resolution and labelling.
 *
 * `Record<EventType, string>` indexing gives TypeScript false confidence: the
 * map is total only for values the type system already checked. An untyped or
 * legacy payload arrives at runtime unchecked and would index to `undefined`,
 * rendering a blank cell. `eventTypeLabel` is total for *every* input.
 */
import type { EventType } from "@/types/event";
import { translate } from "../i18n/translate.ts";

/**
 * Legacy wire names, translated explicitly rather than rendered as-is.
 *
 * "alarm" was always `value == "1"`, so it is a safe rename. "system" was the
 * everything-else bucket and cannot be told apart from a genuine recovery, so
 * it degrades to "unknown" instead of asserting a fact the payload never had.
 * Mirrors LEGACY_EVENT_TYPE_ALIASES in backend/zabbix_client.py.
 */
const LEGACY_EVENT_TYPE_ALIASES: Record<string, EventType> = {
  alarm: "alarm_created",
  system: "unknown",
};

/**
 * Raw Zabbix trigger values. Zabbix `event.get` returns `value` as "1" for a
 * problem raised and "0" for it resolved, and an older backend may still put
 * that on the wire instead of a canonical type. Mirrors
 * canonical_event_type in backend/zabbix_client.py; the two must not drift.
 */
const TRIGGER_VALUE_TYPES: Record<string, EventType> = {
  "1": "alarm_created",
  "0": "alarm_resolved",
};

/** Dictionary keys, not copy; `canonicalEventType` also uses the key set. */
const EVENT_TYPE_LABELS: Record<EventType, string> = {
  alarm_created:  "event.alarmCreated",
  alarm_resolved: "event.alarmResolved",
  status_change:  "event.statusChange",
  config_change:  "event.configChange",
  reboot:         "event.reboot",
  unknown:        "event.unknown",
};

/** Coerce any incoming token onto the canonical union; never throws. */
export function canonicalEventType(raw: unknown): EventType {
  if (typeof raw !== "string") return "unknown";
  const token = raw.trim();
  if (token in EVENT_TYPE_LABELS) return token as EventType;
  if (token in TRIGGER_VALUE_TYPES) return TRIGGER_VALUE_TYPES[token];
  return LEGACY_EVENT_TYPE_ALIASES[token] ?? "unknown";
}

/** Display label for any event type. Total: no input can produce a blank cell. */
export function eventTypeLabel(raw: unknown): string {
  return translate(EVENT_TYPE_LABELS[canonicalEventType(raw)]);
}

/**
 * Message text, guarded against a blank cell. A partial event with no `name`
 * still needs to render as something, not as an empty column.
 */
export function eventMessage(raw: unknown): string {
  return typeof raw === "string" && raw.trim() ? raw : translate("event.unnamed");
}
