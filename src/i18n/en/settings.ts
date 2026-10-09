import type { Dict } from "../types";

/**
 * Settings page: account, password, user administration, appearance,
 * backend connections and system facts.
 *
 * Values the deployment owns (version, build date, timezone id, service state
 * tokens) stay as rendered data, not copy.
 */
const settings: Dict = {
  "settings.title": "Settings",
  "settings.subtitle": "System configuration, access control, and deployment status",

  // Account & Security
  "settings.account.title": "Account & Security",
  "settings.account.signedIn": "Signed in as",
  "settings.account.signedInTail": "({role}) via Local Authentication",
  "settings.account.changePassword": "Change Account Password",
  "settings.account.currentPassword": "Current Password",
  "settings.account.newPassword": "New Password (min 8 chars)",
  "settings.account.confirmPassword": "Confirm New Password",
  "settings.account.updatePassword": "Update Password",
  "settings.account.updating": "Updating...",
  "settings.password.minLength": "New password must be at least 8 characters.",
  "settings.password.mismatch": "New passwords do not match.",
  "settings.password.unchanged": "New password must differ from current password.",
  "settings.password.changed": "Password changed.",
  "settings.password.changeFailed": "Failed to change password",

  // User Management (admin)
  "settings.users.title": "User Management",
  "settings.users.description": "Manage user roles, access, and credentials",
  "settings.users.createTitle": "Create New User",
  "settings.users.email": "Email",
  "settings.users.emailPlaceholder": "user@example.com",
  "settings.users.name": "Name",
  "settings.users.namePlaceholder": "Full Name",
  "settings.users.tempPassword": "Temporary Password (min 8)",
  "settings.users.role": "Role",
  "settings.users.add": "Add User",
  "settings.users.save": "Save User",
  "settings.users.saving": "Creating...",
  "settings.users.minLength": "Password must be at least 8 characters.",
  "settings.users.created": "User {email} created.",
  "settings.users.enabled": "User {email} enabled.",
  "settings.users.disabled": "User {email} disabled.",
  "settings.users.roleUpdated": "Role for {email} updated to {role}.",
  "settings.users.passwordReset": "Password reset and sessions revoked for {email}.",
  "settings.users.sessionsRevoked": "Sessions revoked for {email}.",
  "settings.users.loadFailed": "Failed to load user list",
  "settings.users.createFailed": "Failed to create user",
  "settings.users.statusFailed": "Failed to update user status",
  "settings.users.roleFailed": "Failed to update user role",
  "settings.users.resetFailed": "Failed to reset password",
  "settings.users.revokeFailed": "Failed to revoke sessions",
  "settings.users.resetTitle": "Reset Password for {email}",
  "settings.users.resetPasswordLabel": "New Temporary Password (min 8 characters)",
  "settings.users.applyReset": "Apply Password Reset",
  "settings.users.resetting": "Resetting...",
  "settings.users.tableStatus": "Status",
  "settings.users.mustChangePass": "Must Change Pass",
  "settings.users.tableActions": "Actions",
  "settings.users.loading": "Loading users...",
  "settings.users.empty": "No users found.",
  "settings.users.stateActive": "Active",
  "settings.users.stateDisabled": "Disabled",
  "settings.users.yes": "Yes",
  "settings.users.no": "No",
  "settings.users.enable": "Enable",
  "settings.users.disable": "Disable",
  "settings.users.resetPass": "Reset Pass",
  "settings.users.revoke": "Revoke",
  "settings.users.cancel": "Cancel",

  // Appearance
  "settings.appearance.title": "Appearance",
  "settings.appearance.theme": "Theme",
  "settings.appearance.light": "Light",
  "settings.appearance.dark": "Dark",
  "settings.appearance.system": "System",

  // Alarm sound
  "settings.alarmSound.title": "Alarm Sound",
  "settings.alarmSound.label": "Audible alerts",
  "settings.alarmSound.description": "Plays a short tone for high and disaster alarms in this browser.",
  "settings.alarmSound.on": "On",
  "settings.alarmSound.off": "Off",
  "settings.alarmSound.test": "Test",

  // Timezone
  "settings.timezone.title": "Timezone",
  "settings.timezone.display": "Display Timezone",
  "settings.timezone.fixed": "Timezone is fixed for this deployment.",

  // Backend connections
  "settings.integrations.title": "Backend Connections",
  "settings.integrations.service": "Service",
  "settings.integrations.status": "Status",
  "settings.integrations.mqtt": "MQTT Collector",
  "settings.integrations.zabbixApi": "Zabbix API",
  "settings.integrations.zabbixSender": "Zabbix Sender",
  "settings.integrations.sse": "SSE",

  // System
  "settings.system.title": "System",
  "settings.system.version": "NMS Version",
  "settings.system.buildDate": "Build Date",
  "settings.system.deployment": "Deployment",
};

export default settings;
