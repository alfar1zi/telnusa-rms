import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import PageHeader from "@/components/layout/PageHeader";
import EmptyState from "@/components/common/EmptyState";
import LoadingState from "@/components/common/LoadingState";
import ErrorState from "@/components/common/ErrorState";
import { AlertTriangle } from "lucide-react";
import { useI18n } from "@/i18n/I18nContext";
import { get } from "@/services/http";
import { useDeviceLabel } from "@/hooks/useDeviceLabel";
import {
  REMOTE_FRAME_ALLOW, REMOTE_FRAME_REFERRER_POLICY, REMOTE_FRAME_SANDBOX,
} from "@/lib/remoteFrame";

export default function RemoteWebUI() {
  const { deviceId } = useParams<{ deviceId: string }>();
  const { t } = useI18n();
  const deviceLabel = useDeviceLabel(deviceId);
  const [status, setStatus] = useState<"loading" | "blocked" | "ready" | "error">("loading");
  // Server-supplied detail only. This module's own failure copy is chosen per
  // status at render, so it follows a language change without refetching the
  // remote session.
  const [message, setMessage] = useState<string>("");
  const [remoteUrl, setRemoteUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!deviceId) return;
    get<{ url?: string; status?: string }>(`/remote/${encodeURIComponent(deviceId)}/webui`)
      .then((res) => {
        if (res?.url) {
          setRemoteUrl(res.url);
          setStatus("ready");
        } else {
          setStatus("blocked");
          setMessage("");
        }
      })
      .catch((err: { status?: number; message?: string }) => {
        if (err?.status === 501 || err?.status === 503) {
          setStatus("blocked");
          setMessage(err.message || "");
        } else {
          setStatus("error");
          setMessage(err?.message || "");
        }
      });
  }, [deviceId]);

  return (
    <>
      <PageHeader
        title={t("remote.webUiTitle", { device: deviceLabel || deviceId || t("remote.deviceFallback") })}
        subtitle={t("remote.webUiSubtitle")}
      />
      {status === "loading" && <LoadingState />}
      {status === "blocked" && (
        <EmptyState
          icon={AlertTriangle}
          message={message || t("remote.webUiBlocked")}
        />
      )}
      {status === "error" && <ErrorState hint={t("common.contactAdmin")} message={message || t("remote.webUiFailed")} />}
      {status === "ready" && remoteUrl && (
        <div className="w-full h-[700px] border rounded-lg overflow-hidden bg-background shadow-[0_1px_3px_0_rgba(0,0,0,0.05),0_1px_2px_-1px_rgba(0,0,0,0.04),inset_0_1px_0_0_rgba(255,255,255,0.85)] dark:shadow-[0_4px_16px_0_rgba(0,0,0,0.45),0_1px_3px_0_rgba(0,0,0,0.35),inset_0_1px_0_0_rgba(255,255,255,0.08)] dark:border-white/[0.09]">
          <iframe
            src={remoteUrl}
            title={t("remote.webUiTitle", { device: deviceLabel || deviceId || "" })}
            className="w-full h-full border-0"
            sandbox={REMOTE_FRAME_SANDBOX}
            allow={REMOTE_FRAME_ALLOW}
            referrerPolicy={REMOTE_FRAME_REFERRER_POLICY}
          />
        </div>
      )}
    </>
  );
}
