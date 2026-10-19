import { useEffect, useRef, useState } from "react";
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

export default function RemoteTerminal() {
  const { deviceId } = useParams<{ deviceId: string }>();
  const { t } = useI18n();
  const deviceLabel = useDeviceLabel(deviceId);
  const [status, setStatus] = useState<"loading" | "blocked" | "ready" | "error">("loading");
  // Server-supplied detail only. This module's own failure copy is chosen per
  // status at render, so it follows a language change without refetching the
  // remote session.
  const [message, setMessage] = useState<string>("");
  const [terminalUrl, setTerminalUrl] = useState<string | null>(null);

  // the Guacamole client attaches its keyboard handler to the iframe
  // window. Without window focus, keypresses never reach the client even
  // though the read path renders. Focus once per granted URL (fresh load and
  // reconnect each mint a new URL); never poll or steal focus afterwards.
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const focusedUrlRef = useRef<string | null>(null);
  const focusTerminal = () => {
    const frame = frameRef.current;
    if (!frame || focusedUrlRef.current === terminalUrl) return;
    try {
      frame.contentWindow?.focus();
      focusedUrlRef.current = terminalUrl;
    } catch {
      // Cross-origin or not-yet-attached window: leave unfocused rather than
      // throwing; a normal click inside the terminal still works.
    }
  };

  useEffect(() => {
    if (!deviceId) return;
    get<{ url?: string; tunnelId?: string; status?: string }>(`/remote/${encodeURIComponent(deviceId)}/terminal`)
      .then((res) => {
        if (res?.url) {
          setTerminalUrl(res.url);
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
        title={t("remote.terminalTitle", { device: deviceLabel || deviceId || t("remote.deviceFallback") })}
        subtitle={t("remote.terminalSubtitle")}
      />
      {status === "loading" && <LoadingState />}
      {status === "blocked" && (
        <EmptyState
          icon={AlertTriangle}
          message={message || t("remote.terminalBlocked")}
        />
      )}
      {status === "error" && <ErrorState hint={t("common.contactAdmin")} message={message || t("remote.terminalFailed")} />}
      {status === "ready" && terminalUrl && (
        <div className="w-full h-[700px] border rounded-lg overflow-hidden bg-background shadow-[0_1px_3px_0_rgba(0,0,0,0.05),0_1px_2px_-1px_rgba(0,0,0,0.04),inset_0_1px_0_0_rgba(255,255,255,0.85)] dark:shadow-[0_4px_16px_0_rgba(0,0,0,0.45),0_1px_3px_0_rgba(0,0,0,0.35),inset_0_1px_0_0_rgba(255,255,255,0.08)] dark:border-white/[0.09]">
          <iframe
            ref={frameRef}
            onLoad={focusTerminal}
            src={terminalUrl}
            title={t("remote.terminalTitle", { device: deviceLabel || deviceId || "" })}
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
