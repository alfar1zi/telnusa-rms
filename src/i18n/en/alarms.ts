import type { Dict } from "../types";

/**
 * Alarms page, alarm table, event table and the alarm action dialog.
 *
 * Lifecycle tokens (OPEN / ACK'D / RESOLVED) stay uppercase in both languages:
 * they are the console's status vocabulary, read as chips next to a coloured
 * border, not prose. Severity words are shared with the badge through core's
 * `badge.severity.*`, and `state.noData` / `common.cancel` likewise, so one word
 * is not written twice.
 */
const alarms: Dict = {
  // Page shell
  "alarms.title": "Alarms",
  "alarms.subtitle": "Canonical alarm engineering, lifecycle management, and rule evaluation",
  "alarms.unavailable": "Alarms Unavailable",
  "alarms.filterPlaceholder": "Filter problem, device...",
  "alarms.allDevices": "All Devices",
  "alarms.allGateways": "All Gateways",
  "alarms.allSeverities": "All Severities",
  "alarms.actionFailed": "Action failed",

  // Alarm sound
  "alarms.sound.on": "Alarm Sound On",
  "alarms.sound.off": "Alarm Sound Off",

  // Tabs
  "alarms.tab.active": "Active Alarms ({count})",
  "alarms.tab.history": "Resolved History ({count})",
  "alarms.tab.rules": "Engine Rules ({count})",

  // Lifecycle state tokens
  "alarms.state.open": "OPEN",
  "alarms.state.acknowledged": "ACK'D",
  "alarms.state.resolved": "RESOLVED",

  // Alarm table
  "alarms.table.triggered": "Triggered / Last Seen",
  "alarms.table.device": "Device",
  "alarms.table.site": "Site",
  "alarms.table.problem": "Problem & Context",
  "alarms.table.severity": "Severity",
  "alarms.table.state": "State",
  "alarms.table.actions": "Actions",
  "alarms.table.firstSeen": "First: {time}",
  "alarms.table.ackBy": "Ack'd by {operator}",
  "alarms.table.ackByNote": "Ack'd by {operator} (\"{note}\")",
  "alarms.table.resolvedBy": "Resolved by {operator}",
  "alarms.table.resolvedByNote": "Resolved by {operator} (\"{note}\")",
  "alarms.empty.noMatch": "No alarms found matching criteria.",
  "alarms.empty.none": "No alarms. Every monitored device is reporting within its thresholds.",
  "alarms.empty.history": "No resolved alarms recorded yet.",

  // Row actions
  "alarms.action.ack": "Ack",
  "alarms.action.resolve": "Resolve",

  // Engine rules tab
  "alarms.rules.enabled": "ENABLED",
  "alarms.rules.disabled": "DISABLED",
  "alarms.rules.summary": "Rule ID: {id} • Metric: {metric} {operator} {threshold} (scope: {pattern})",
  "alarms.rules.schedule": "Interval: {interval}s • Consecutive: {consecutive}",
  "alarms.rules.noThreshold": "NONE",

  // Acknowledge / resolve dialog
  "alarms.dialog.ackTitle": "Acknowledge Alarm",
  "alarms.dialog.resolveTitle": "Resolve Alarm",
  "alarms.dialog.subject": "{problem} on {deviceId}",
  "alarms.dialog.ackNoteLabel": "Operator Note (optional)",
  "alarms.dialog.resolveReasonLabel": "Resolution Reason (optional)",
  "alarms.dialog.ackNotePlaceholder": "e.g. Inspecting battery terminal...",
  "alarms.dialog.resolveReasonPlaceholder": "e.g. Cleared after cable reseat",
  "alarms.dialog.confirmAck": "Confirm Ack",
  "alarms.dialog.confirmResolve": "Confirm Resolve",
  "alarms.dialog.processing": "Processing...",

  // Device event table
  "alarms.events.time": "Time",
  "alarms.events.type": "Type",
  "alarms.events.message": "Message",
  "alarms.events.empty": "No events recorded.",
};

export default alarms;
