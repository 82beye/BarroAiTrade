import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface StatCardProps {
  title: string;
  value: string | number;
  suffix?: string;
  color?: 'default' | 'success' | 'danger' | 'warning';
  icon?: React.ReactNode;
}

export function StatCard({
  title,
  value,
  suffix,
  color = 'default',
  icon,
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
        <CardTitle className="text-sm font-medium text-slate-400">
          {title}
        </CardTitle>
        {icon && <div className="text-xl">{icon}</div>}
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-bold ${colorClasses[color]}`}>
          {value}
          {suffix && <span className="text-sm text-slate-400">{suffix}</span>}
        </div>
      </CardContent>
    </Card>
  );
}
