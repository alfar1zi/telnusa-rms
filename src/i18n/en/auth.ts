import type { Dict } from "../types";

/**
 * Authentication screens and secure remote access.
 *
 * `auth.*` covers the login screen states and the forced password change
 * screen; `remote.*` covers the remote access card, the remote Web UI and the
 * remote terminal page. The login page's static chrome (brand, product line,
 * field labels, primary action) is already published as `login.*` in the
 * shared core slice and the two password-change failure messages as
 * `auth.passwordChangeFailed*`; this slice adds only what core does not own.
 *
 * Values the server owns (the account's own e-mail address, the device id,
 * error bodies returned by the API) stay as rendered data, not copy. The
 * "BLOCKED: ..." lines are console status vocabulary and keep their English
 * token in both languages, as core does for "NO DATA".
 */
const auth: Dict = {
  // Login form states
  "auth.showPassword": "Show password",
  "auth.hidePassword": "Hide password",
  "auth.invalidCredentials": "Invalid credentials.",
  "auth.tooManyAttempts": "Too many attempts. Try again later.",
  "auth.signInFailed": "Sign in failed. Try again.",
  "auth.serverUnreachable": "Cannot reach the server. Check the network connection.",
  "auth.emailPlaceholder": "name@example.com",
  "auth.emailPlaceholderMock": "admin@telnusa.dev",
  "auth.submitMock": "Sign in (Mock Mode)",
  "auth.mockModeNotice": "Development mock mode: no credentials required",

  // Forced password change
  "auth.securityRequirement": "Security Requirement",
  "auth.changeRequiredTitle": "Change Password Required",
  "auth.changeRequiredPrefix": "Account ",
  "auth.changeRequiredSuffix": " requires a password update before accessing TELNUSA NMS.",
  "auth.currentPassword": "Current Password",
  "auth.newPassword": "New Password (min 8 chars)",
  "auth.confirmPassword": "Confirm New Password",
  "auth.passwordTooShort": "New password must be at least 8 characters long.",
  "auth.passwordUnchanged": "New password must be different from current password.",
  "auth.passwordMismatch": "New passwords do not match.",
  "auth.passwordUpdated": "Password updated.",
  "auth.redirecting": "Redirecting to dashboard...",
  "auth.updatingPassword": "Updating Password...",
  "auth.updatePasswordAndContinue": "Update Password & Continue",
  "auth.signOut": "Sign Out",

  // Remote access card
  "remote.webUi": "Web UI",
  "remote.webUiDesc": "Native Teltonika RUT906 WebUI",
  "remote.terminal": "Terminal",
  "remote.terminalDesc": "SSH via Apache Guacamole",
  "remote.zabbix": "Zabbix",
  "remote.zabbixDesc": "Monitoring and history",
  "remote.zabbixInternalOnly": "Direct Zabbix access is internal-only",
  "remote.notConfigured": "Not Configured",
  "remote.open": "Open",

  // Remote Web UI and terminal pages
  "remote.deviceFallback": "Device",
  "remote.webUiTitle": "Remote Web UI: {device}",
  "remote.webUiSubtitle": "Native Teltonika RUT906 Web Management Interface",
  "remote.webUiBlocked": "BLOCKED: MISSING EXTERNAL SERVICE (ZeroTier / RUT WebUI proxy unconfigured).",
  "remote.webUiFailed": "Failed to establish remote Web UI connection.",
  "remote.terminalTitle": "Remote Terminal: {device}",
  "remote.terminalSubtitle": "Secure SSH Terminal via Apache Guacamole",
  "remote.terminalBlocked": "BLOCKED: MISSING EXTERNAL SERVICE (Guacamole / SSH tunnel unconfigured).",
  "remote.terminalFailed": "Failed to establish remote terminal connection.",
};

export default auth;
