import { useNavigate } from "react-router-dom";
import { TextureButton } from "@/components/ui/texture-button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Globe, Terminal, Activity } from "lucide-react";
import { useI18n } from "@/i18n/I18nContext";

interface RemoteAccessCardProps {
  deviceId: string;
}

export default function RemoteAccessCard({ deviceId }: RemoteAccessCardProps) {
  const navigate = useNavigate();
  const { t } = useI18n();
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card className="flex flex-col justify-between">
        <CardHeader className="p-4 sm:p-5 pb-2">
          <div className="flex items-center gap-2">
            <Globe className="size-4 text-muted-foreground shrink-0" />
            <CardTitle className="text-sm font-semibold">{t("remote.webUi")}</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="p-4 sm:p-5 pt-0 space-y-3 flex-1 flex flex-col justify-between">
          <p className="text-xs text-muted-foreground">{t("remote.webUiDesc")}</p>
          <TextureButton size="sm" className="w-full sm:w-auto self-start" onClick={() => navigate(`/remote/${deviceId}/webui`)}>{t("remote.open")}</TextureButton>
        </CardContent>
      </Card>
      <Card className="flex flex-col justify-between">
        <CardHeader className="p-4 sm:p-5 pb-2">
          <div className="flex items-center gap-2">
            <Terminal className="size-4 text-muted-foreground shrink-0" />
            <CardTitle className="text-sm font-semibold">{t("remote.terminal")}</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="p-4 sm:p-5 pt-0 space-y-3 flex-1 flex flex-col justify-between">
          <p className="text-xs text-muted-foreground">{t("remote.terminalDesc")}</p>
          <TextureButton size="sm" className="w-full sm:w-auto self-start" onClick={() => navigate(`/remote/${deviceId}/terminal`)}>{t("remote.open")}</TextureButton>
        </CardContent>
      </Card>
      <Card className="flex flex-col justify-between">
        <CardHeader className="p-4 sm:p-5 pb-2">
          <div className="flex items-center gap-2">
            <Activity className="size-4 text-muted-foreground shrink-0" />
            <CardTitle className="text-sm font-semibold">{t("remote.zabbix")}</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="p-4 sm:p-5 pt-0 space-y-3 flex-1 flex flex-col justify-between">
          <p className="text-xs text-muted-foreground">{t("remote.zabbixDesc")}</p>
          <TextureButton size="sm" variant="secondary" disabled title={t("remote.zabbixInternalOnly")} className="w-full sm:w-auto self-start">{t("remote.notConfigured")}</TextureButton>
        </CardContent>
      </Card>
    </div>
  );
}
