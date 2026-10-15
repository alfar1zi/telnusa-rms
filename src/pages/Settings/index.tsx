import { useEffect, useState, useCallback, type FormEvent } from "react";
import { TextureButton } from "@/components/ui/texture-button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  SelectGroup,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { type Theme, applyTheme, getStoredTheme, storeTheme } from "@/lib/theme";
import PageHeader from "@/components/layout/PageHeader";
import {
  getIntegrationStatus,
  getUsers,
  createUser,
  updateUser,
  resetUserPassword,
  revokeUserSessions,
  changePassword,
  type IntegrationStatus,
} from "@/services/api";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/i18n/I18nContext";
import { useAlarmSound } from "@/hooks/useAlarmSound";
import { Bell } from "lucide-react";
import type { UserRecord } from "@/types/auth";
import type { UserRole } from "@/types/device";
function statusText(status: IntegrationStatus | undefined, component: "mqtt" | "zabbix"): string {
  const state = status?.[component]?.state;
  return state ? state : "UNKNOWN";
}

function sseText(status: IntegrationStatus | undefined): string {
  if (!status?.sse) return "UNKNOWN";
  return status.sse.clients && status.sse.clients > 0 ? "ACTIVE" : "IDLE";
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2 border-b border-border/40 last:border-0 text-xs sm:text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

export default function Settings() {
  const { user, refresh: refreshAuth } = useAuth();
  const { t } = useI18n();
  const alarmSound = useAlarmSound();
  const [theme, setTheme] = useState<Theme>(getStoredTheme);
  const [integration, setIntegration] = useState<IntegrationStatus | undefined>(undefined);
  const [integrationError, setIntegrationError] = useState<Error | null>(null);
  // User management state (Admin only)
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [userMsg, setUserMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // New user form state
  const [showAddUser, setShowAddUser] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("viewer");
  const [addingUser, setAddingUser] = useState(false);

  // Password reset modal state
  const [resetTargetUser, setResetTargetUser] = useState<UserRecord | null>(null);
  const [adminNewPass, setAdminNewPass] = useState("");
  const [resettingPass, setResettingPass] = useState(false);

  // Personal password change state
  const [currPass, setCurrPass] = useState("");
  const [nextPass, setNextPass] = useState("");
  const [confirmPass, setConfirmPass] = useState("");
  const [changingPass, setChangingPass] = useState(false);
  const [passMsg, setPassMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const loadUserList = useCallback(async () => {
    if (user?.role !== "admin") return;
    setLoadingUsers(true);
    try {
      const list = await getUsers();
      setUsers(list);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.users.loadFailed");
      setUserMsg({ type: "error", text: msg });
    } finally {
      setLoadingUsers(false);
    }
  }, [user?.role, t]);

  useEffect(() => {
    let live = true;
    getIntegrationStatus()
      .then((status) => {
        if (live) {
          setIntegration(status);
          setIntegrationError(null);
        }
      })
      .catch((err: Error) => {
        if (live) {
          setIntegrationError(err);
          setIntegration(undefined);
        }
      });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (user?.role === "admin") {
      void loadUserList();
    }
  }, [user?.role, loadUserList]);

  function handleTheme(val: Theme) {
    applyTheme(val);
    storeTheme(val);
    setTheme(val);
  }

  async function handleAddUser(e: FormEvent) {
    e.preventDefault();
    setUserMsg(null);
    if (newPassword.length < 8) {
      setUserMsg({ type: "error", text: t("settings.users.minLength") });
      return;
    }
    setAddingUser(true);
    try {
      await createUser({
        email: newEmail.trim(),
        name: newName.trim(),
        password: newPassword,
        role: newRole,
      });
      setUserMsg({ type: "success", text: t("settings.users.created", { email: newEmail }) });
      setNewEmail("");
      setNewName("");
      setNewPassword("");
      setNewRole("viewer");
      setShowAddUser(false);
      await loadUserList();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.users.createFailed");
      setUserMsg({ type: "error", text: msg });
    } finally {
      setAddingUser(false);
    }
  }

  async function handleToggleDisable(target: UserRecord) {
    setUserMsg(null);
    try {
      await updateUser(target.id, { disabled: !target.disabled });
      setUserMsg({
        type: "success",
        text: target.disabled
          ? t("settings.users.enabled", { email: target.email })
          : t("settings.users.disabled", { email: target.email }),
      });
      await loadUserList();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.users.statusFailed");
      setUserMsg({ type: "error", text: msg });
    }
  }

  async function handleRoleChange(target: UserRecord, role: UserRole) {
    setUserMsg(null);
    try {
      await updateUser(target.id, { role });
      setUserMsg({
        type: "success",
        text: t("settings.users.roleUpdated", { email: target.email, role }),
      });
      await loadUserList();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.users.roleFailed");
      setUserMsg({ type: "error", text: msg });
    }
  }

  async function handleResetPassword(e: FormEvent) {
    e.preventDefault();
    if (!resetTargetUser) return;
    if (adminNewPass.length < 8) {
      setUserMsg({ type: "error", text: t("settings.users.minLength") });
      return;
    }
    setResettingPass(true);
    try {
      await resetUserPassword(resetTargetUser.id, adminNewPass);
      setUserMsg({
        type: "success",
        text: t("settings.users.passwordReset", { email: resetTargetUser.email }),
      });
      setResetTargetUser(null);
      setAdminNewPass("");
      await loadUserList();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.users.resetFailed");
      setUserMsg({ type: "error", text: msg });
    } finally {
      setResettingPass(false);
    }
  }

  async function handleRevokeSessions(target: UserRecord) {
    setUserMsg(null);
    try {
      await revokeUserSessions(target.id);
      setUserMsg({ type: "success", text: t("settings.users.sessionsRevoked", { email: target.email }) });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.users.revokeFailed");
      setUserMsg({ type: "error", text: msg });
    }
  }

  async function handleChangeSelfPassword(e: FormEvent) {
    e.preventDefault();
    setPassMsg(null);
    if (nextPass.length < 8) {
      setPassMsg({ type: "error", text: t("settings.password.minLength") });
      return;
    }
    if (nextPass !== confirmPass) {
      setPassMsg({ type: "error", text: t("settings.password.mismatch") });
      return;
    }
    if (nextPass === currPass) {
      setPassMsg({ type: "error", text: t("settings.password.unchanged") });
      return;
    }
    setChangingPass(true);
    try {
      await changePassword({ currentPassword: currPass, newPassword: nextPass });
      setPassMsg({ type: "success", text: t("settings.password.changed") });
      setCurrPass("");
      setNextPass("");
      setConfirmPass("");
      await refreshAuth();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t("settings.password.changeFailed");
      setPassMsg({ type: "error", text: msg });
    } finally {
      setChangingPass(false);
    }
  }
  return (
    <>
      <PageHeader
        title={t("settings.title")}
        subtitle={t("settings.subtitle")}
      />

      <div className="space-y-6">
        {/* Account & Security */}
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.account.title")}</CardTitle>
            <CardDescription>
              {t("settings.account.signedIn")} <strong>{user?.email}</strong>{" "}
              {t("settings.account.signedInTail", { role: user?.role ?? "" })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={handleChangeSelfPassword} className="space-y-3 max-w-md">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("settings.account.changePassword")}
              </p>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("settings.account.currentPassword")}</label>
                <input
                  type="password"
                  required
                  value={currPass}
                  onChange={(e) => setCurrPass(e.target.value)}
                  disabled={changingPass}
                  className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("settings.account.newPassword")}</label>
                <input
                  type="password"
                  required
                  value={nextPass}
                  onChange={(e) => setNextPass(e.target.value)}
                  disabled={changingPass}
                  className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("settings.account.confirmPassword")}</label>
                <input
                  type="password"
                  required
                  value={confirmPass}
                  onChange={(e) => setConfirmPass(e.target.value)}
                  disabled={changingPass}
                  className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                />
              </div>
              {passMsg && (
                <p className={`text-xs p-2 rounded border ${passMsg.type === "success" ? "text-foreground bg-secondary/50 border-border" : "text-[var(--destructive)] bg-[var(--destructive)]/10 border-[var(--destructive)]/20"}`}>
                  {passMsg.text}
                </p>
              )}
              <TextureButton type="submit" size="sm" disabled={changingPass}>
                {changingPass ? t("settings.account.updating") : t("settings.account.updatePassword")}
              </TextureButton>
            </form>
          </CardContent>
        </Card>

        {/* User Management (Admin Only) */}
        {user?.role === "admin" && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
              <div>
                <CardTitle>{t("settings.users.title")}</CardTitle>
                <CardDescription>{t("settings.users.description")}</CardDescription>
              </div>
              <TextureButton
                size="sm"
                variant={showAddUser ? "minimal" : "primary"}
                onClick={() => setShowAddUser(!showAddUser)}
              >
                {showAddUser ? t("settings.users.cancel") : t("settings.users.add")}
              </TextureButton>
            </CardHeader>
            <CardContent className="space-y-4">
              {userMsg && (
                <p className={`text-xs p-2 rounded border ${userMsg.type === "success" ? "text-foreground bg-secondary/50 border-border" : "text-[var(--destructive)] bg-[var(--destructive)]/10 border-[var(--destructive)]/20"}`}>
                  {userMsg.text}
                </p>
              )}

              {/* Add User Form */}
              {showAddUser && (
                <form onSubmit={handleAddUser} className="p-4 border rounded-md bg-muted/30 space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wider">{t("settings.users.createTitle")}</p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("settings.users.email")}</label>
                      <input
                        type="email"
                        required
                        value={newEmail}
                        onChange={(e) => setNewEmail(e.target.value)}
                        placeholder={t("settings.users.emailPlaceholder")}
                        className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("settings.users.name")}</label>
                      <input
                        type="text"
                        required
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        placeholder={t("settings.users.namePlaceholder")}
                        className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("settings.users.tempPassword")}</label>
                      <input
                        type="password"
                        required
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">{t("settings.users.role")}</label>
                      <Select value={newRole} onValueChange={(v) => setNewRole(v as UserRole)}>
                        <SelectTrigger className="w-full h-9">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value="admin">admin</SelectItem>
                            <SelectItem value="operator">operator</SelectItem>
                            <SelectItem value="viewer">viewer</SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <TextureButton type="submit" size="sm" disabled={addingUser}>
                    {addingUser ? t("settings.users.saving") : t("settings.users.save")}
                  </TextureButton>
                </form>
              )}

              {/* Admin Reset Password Modal */}
              {resetTargetUser && (
                <form onSubmit={handleResetPassword} className="p-4 border border-border rounded-md bg-secondary/30 space-y-3">
                  <div className="flex justify-between items-center">
                    <p className="text-xs font-semibold text-foreground uppercase tracking-wider">
                      {t("settings.users.resetTitle", { email: resetTargetUser.email })}
                    </p>
                    <TextureButton size="sm" variant="ghost" type="button" onClick={() => setResetTargetUser(null)}>
                      {t("settings.users.cancel")}
                    </TextureButton>
                  </div>
                  <div className="space-y-1 max-w-sm">
                    <label className="text-xs text-muted-foreground">{t("settings.users.resetPasswordLabel")}</label>
                    <input
                      type="password"
                      required
                      value={adminNewPass}
                      onChange={(e) => setAdminNewPass(e.target.value)}
                      className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                    />
                  </div>
                  <TextureButton type="submit" size="sm" disabled={resettingPass}>
                    {resettingPass ? t("settings.users.resetting") : t("settings.users.applyReset")}
                  </TextureButton>
                </form>
              )}

              {/* Users Table */}
              <div className="rounded-md border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("settings.users.email")}</TableHead>
                      <TableHead>{t("settings.users.name")}</TableHead>
                      <TableHead>{t("settings.users.role")}</TableHead>
                      <TableHead>{t("settings.users.tableStatus")}</TableHead>
                      <TableHead>{t("settings.users.mustChangePass")}</TableHead>
                      <TableHead className="text-right">{t("settings.users.tableActions")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loadingUsers ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center py-4 text-sm text-muted-foreground">
                          {t("settings.users.loading")}
                        </TableCell>
                      </TableRow>
                    ) : users.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center py-4 text-sm text-muted-foreground">
                          {t("settings.users.empty")}
                        </TableCell>
                      </TableRow>
                    ) : (
                      users.map((u) => (
                        <TableRow key={u.id}>
                          <TableCell className="text-xs font-medium">{u.email}</TableCell>
                          <TableCell className="text-sm">{u.name}</TableCell>
                          <TableCell>
                            <Select
                              value={u.role}
                              onValueChange={(val) => void handleRoleChange(u, val as UserRole)}
                              disabled={u.id === user?.id}
                            >
                              <SelectTrigger className="w-28 h-7 text-xs">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectGroup>
                                  <SelectItem value="admin">admin</SelectItem>
                                  <SelectItem value="operator">operator</SelectItem>
                                  <SelectItem value="viewer">viewer</SelectItem>
                                </SelectGroup>
                              </SelectContent>
                            </Select>
                          </TableCell>
                          <TableCell>
                            <Badge variant={u.disabled ? "destructive" : "default"}>
                              {u.disabled ? t("settings.users.stateDisabled") : t("settings.users.stateActive")}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {u.mustChangePassword ? (
                              <span className="text-xs font-medium text-[var(--chart-1)]">{t("settings.users.yes")}</span>
                            ) : (
                              <span className="text-xs text-muted-foreground">{t("settings.users.no")}</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right whitespace-nowrap">
                            {/* flex, not inline flow: three row actions must never
                                wrap onto a second line when a scrollbar narrows
                                the table by a few pixels. */}
                            <div className="flex justify-end gap-1">
                            <TextureButton
                              size="sm"
                              variant="minimal"
                              className="h-7"
                              onClick={() => void handleToggleDisable(u)}
                              disabled={u.id === user?.id}
                            >
                              {u.disabled ? t("settings.users.enable") : t("settings.users.disable")}
                            </TextureButton>
                            <TextureButton
                              size="sm"
                              variant="secondary"
                              className="h-7"
                              onClick={() => { setResetTargetUser(u); setAdminNewPass(""); }}
                            >
                              {t("settings.users.resetPass")}
                            </TextureButton>
                            <TextureButton
                              size="sm"
                              variant="ghost"
                              className="h-7 text-muted-foreground"
                              onClick={() => void handleRevokeSessions(u)}
                            >
                              {t("settings.users.revoke")}
                            </TextureButton>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader><CardTitle>{t("settings.appearance.title")}</CardTitle></CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <span className="text-sm">{t("settings.appearance.theme")}</span>
              <Select value={theme} onValueChange={(v) => handleTheme(v as Theme)}>
                <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="light">{t("settings.appearance.light")}</SelectItem>
                  <SelectItem value="dark">{t("settings.appearance.dark")}</SelectItem>
                  <SelectItem value="system">{t("settings.appearance.system")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        {/* Alarm sound */}
        <Card>
          <CardHeader><CardTitle>{t("settings.alarmSound.title")}</CardTitle></CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-0.5">
                <p className="text-sm">{t("settings.alarmSound.label")}</p>
                <p className="text-xs text-muted-foreground">{t("settings.alarmSound.description")}</p>
              </div>
              <div className="flex items-center gap-2">
                <TextureButton
                  variant="secondary"
                  size="sm"
                  aria-pressed={alarmSound.enabled}
                  onClick={() => alarmSound.setEnabled(!alarmSound.enabled)}
                >
                  <Bell className="size-3.5" aria-hidden="true" />
                  {alarmSound.enabled ? t("settings.alarmSound.on") : t("settings.alarmSound.off")}
                </TextureButton>
                <TextureButton variant="minimal" size="sm" onClick={alarmSound.testAlarm}>
                  {t("settings.alarmSound.test")}
                </TextureButton>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Timezone */}
        <Card>
          <CardHeader><CardTitle>{t("settings.timezone.title")}</CardTitle></CardHeader>
          <CardContent>
            <InfoRow label={t("settings.timezone.display")} value="Asia/Jakarta (UTC+7)" />
            <p className="text-xs text-muted-foreground mt-2">{t("settings.timezone.fixed")}</p>
          </CardContent>
        </Card>

        {/* Backend Connections */}
        <Card>
          <CardHeader><CardTitle>{t("settings.integrations.title")}</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("settings.integrations.service")}</TableHead>
                  <TableHead>{t("settings.integrations.status")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell className="text-sm">{t("settings.integrations.mqtt")}</TableCell>
                  <TableCell><Badge variant="secondary">{statusText(integration, "mqtt")}</Badge></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="text-sm">{t("settings.integrations.zabbixApi")}</TableCell>
                  <TableCell><Badge variant="secondary">{statusText(integration, "zabbix")}</Badge></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="text-sm">{t("settings.integrations.zabbixSender")}</TableCell>
                  <TableCell><Badge variant={integrationError ? "destructive" : integration?.zabbix?.last_error ? "default" : "secondary"}>{integrationError ? "ERROR" : integration?.zabbix?.last_error ? "DEGRADED" : statusText(integration, "zabbix")}</Badge></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="text-sm">{t("settings.integrations.sse")}</TableCell>
                  <TableCell><Badge variant="secondary">{sseText(integration)}</Badge></TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* System */}
        <Card>
          <CardHeader><CardTitle>{t("settings.system.title")}</CardTitle></CardHeader>
          <CardContent>
            <InfoRow label={t("settings.system.version")} value="1.0.0-dev" />
            <InfoRow label={t("settings.system.buildDate")}  value="2026-09-22" />
            <InfoRow label={t("settings.system.deployment")}  value="Apache (static)" />
          </CardContent>
        </Card>
      </div>
    </>
  );
}
