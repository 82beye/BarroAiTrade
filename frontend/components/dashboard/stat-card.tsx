import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface StatCardProps {
  title: string;
  value: string | number;
  suffix?: string;
  description?: string;
  color?: 'default' | 'success' | 'danger' | 'warning';
  icon?: React.ReactNode;
  live?: boolean;
}

export function StatCard({
  title,
  value,
  suffix,
  description,
  color = 'default',
  icon,
  live = false,
}: StatCardProps) {
  const colorClasses = {
    default: 'text-blue-500',
    success: 'text-green-500',
    danger: 'text-red-500',
    warning: 'text-yellow-500',
  };

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="flex items-center gap-2">
          <CardTitle className="text-sm font-medium text-slate-400">
            {title}
          </CardTitle>
          {live && (
            <span className="flex items-center gap-1 text-xs text-green-400">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-green-400" />
              LIVE
            </span>
          )}
        </div>
        {icon && <div className="text-xl">{icon}</div>}
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-bold ${colorClasses[color]}`}>
          {value}
          {suffix && <span className="text-sm text-slate-400">{suffix}</span>}
        </div>
        {description && (
          <p className="mt-1 text-xs text-slate-500">{description}</p>
        )}
      </CardContent>
    </Card>
  );
}
