import { useNavigate } from "react-router-dom";
import { Compass } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { TextureButton } from "@/components/ui/texture-button";
import PageHeader from "@/components/layout/PageHeader";
import EmptyState from "@/components/common/EmptyState";
import { useI18n } from "@/i18n/I18nContext";

/** Unknown URL: say what happened and offer one way back, inside the normal shell. */
export default function NotFound() {
  const navigate = useNavigate();
  const { t } = useI18n();

  return (
    <div className="space-y-6">
      <PageHeader title={t("notFound.title")} subtitle={t("notFound.subtitle")} />
      <Card>
        <CardContent className="p-0">
          <EmptyState
            icon={Compass}
            message={t("notFound.message")}
            action={
              <TextureButton variant="secondary" size="sm" className="mt-4" onClick={() => navigate("/dashboard")}>
                {t("notFound.action")}
              </TextureButton>
            }
          />
        </CardContent>
      </Card>
    </div>
  );
}
