import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { AuthProvider }    from "@/context/AuthContext";
import ProtectedRoute      from "@/components/layout/ProtectedRoute";
import AppShell            from "@/components/layout/AppShell";
import Login               from "@/pages/Login";
import Dashboard           from "@/pages/Dashboard";
import Devices             from "@/pages/Devices";
import DeviceDetails       from "@/pages/DeviceDetails";
import Monitoring          from "@/pages/Monitoring";
import Alarms              from "@/pages/Alarms";
import Reports             from "@/pages/Reports";
import Settings            from "@/pages/Settings";
import RemoteWebUI         from "@/pages/Remote/WebUI";
import RemoteTerminal      from "@/pages/Remote/Terminal";
import NotFound            from "@/pages/NotFound";

export default function App() {
  return (
    <I18nProvider>
      <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public */}
          <Route path="/login" element={<Login />} />

          {/* Protected: session required */}
          <Route element={<ProtectedRoute />}>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard"                 element={<Dashboard />} />
              <Route path="/devices"                   element={<Devices />} />
              <Route path="/devices/:deviceId"         element={<DeviceDetails />} />
              <Route path="/monitoring"                element={<Monitoring />} />
              <Route path="/alarms"                    element={<Alarms />} />
              <Route path="/reports"                   element={<Reports />} />
              <Route path="/settings"                  element={<Settings />} />
              <Route path="/remote/:deviceId/webui"    element={<RemoteWebUI />} />
              <Route path="/remote/:deviceId/terminal" element={<RemoteTerminal />} />
              <Route path="*"                          element={<NotFound />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
      </BrowserRouter>
    </I18nProvider>
  );
}
