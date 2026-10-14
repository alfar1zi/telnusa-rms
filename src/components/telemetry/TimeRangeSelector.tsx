import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TIME_RANGES, type TimeRangeKey } from "@/utils/timeRange";
import { useI18n } from "@/i18n/I18nContext";

interface TimeRangeSelectorProps {
  value: TimeRangeKey;
  onChange: (key: TimeRangeKey) => void;
}

export default function TimeRangeSelector({ value, onChange }: TimeRangeSelectorProps) {
  const { t } = useI18n();
  return (
    <Select value={value} onValueChange={(v) => onChange(v as TimeRangeKey)}>
      <SelectTrigger className="w-[160px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.entries(TIME_RANGES) as [TimeRangeKey, typeof TIME_RANGES[TimeRangeKey]][]).map(([k, r]) => (
          <SelectItem key={k} value={k}>{t(r.labelKey)}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
