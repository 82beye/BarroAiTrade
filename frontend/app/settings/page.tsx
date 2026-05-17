'use client';

import { useState, useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

const SettingsSchema = z.object({
  stopLoss: z.number().min(0.1, '0.1 이상이어야 합니다').max(50, '50 이하여야 합니다'),
  takeProfit: z.number().min(0.1, '0.1 이상이어야 합니다').max(100, '100 이하여야 합니다'),
  dailyLossLimit: z.number().min(0.1, '0.1 이상이어야 합니다').max(20, '20 이하여야 합니다'),
  maxSymbols: z.number().min(1, '1개 이상이어야 합니다').max(20, '20개 이하여야 합니다'),
  mode: z.enum(['SIMULATION', 'LIVE'], {
    errorMap: () => ({ message: '유효한 모드를 선택해주세요' }),
  }),
  telegramChatId: z.string().optional(),
  notifyOnOrder: z.boolean(),
  notifyOnClose: z.boolean(),
  notifyOnStopLoss: z.boolean(),
});

type SettingsFormData = z.infer<typeof SettingsSchema>;

const DEFAULT_SETTINGS: SettingsFormData = {
  stopLoss: 5.0,
  takeProfit: 10.0,
  dailyLossLimit: 3.0,
  maxSymbols: 5,
  mode: 'SIMULATION',
  telegramChatId: '',
  notifyOnOrder: true,
  notifyOnClose: true,
  notifyOnStopLoss: true,
};

export default function SettingsPage() {
  const [pageLoading, setPageLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle');
  const [saveMessage, setSaveMessage] = useState('');

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<SettingsFormData>({
    resolver: zodResolver(SettingsSchema),
    defaultValues: DEFAULT_SETTINGS,
  });

  useEffect(() => {
    async function loadSettings() {
      try {
        const [riskRes, configRes] = await Promise.all([
          fetch('/api/risk/status'),
          fetch('/api/config'),
        ]);

        const merged: SettingsFormData = { ...DEFAULT_SETTINGS };

        if (riskRes.ok) {
          const risk = await riskRes.json();
          const limits = risk.limits ?? {};
          if (limits.stop_loss_pct != null) merged.stopLoss = limits.stop_loss_pct;
          if (limits.take_profit_1_pct != null) merged.takeProfit = limits.take_profit_1_pct;
          if (limits.daily_loss_limit_pct != null) merged.dailyLossLimit = limits.daily_loss_limit_pct;
          if (limits.max_concurrent_positions != null) merged.maxSymbols = limits.max_concurrent_positions;
        }

        if (configRes.ok) {
          const config = await configRes.json();
          if (config.trading_mode === 'live') merged.mode = 'LIVE';
          else if (config.trading_mode === 'simulation') merged.mode = 'SIMULATION';
          if (config.telegram_chat_id) merged.telegramChatId = config.telegram_chat_id;
          if (config.notify_on_order != null) merged.notifyOnOrder = config.notify_on_order;
          if (config.notify_on_close != null) merged.notifyOnClose = config.notify_on_close;
          if (config.notify_on_stop_loss != null) merged.notifyOnStopLoss = config.notify_on_stop_loss;
        }

        reset(merged);
      } catch {
        reset(DEFAULT_SETTINGS);
      } finally {
        setPageLoading(false);
      }
    }
    loadSettings();
  }, [reset]);

  const onSubmit = async (data: SettingsFormData) => {
    setSaveStatus('saving');
    setSaveMessage('');
    try {
      const [riskRes, configRes] = await Promise.all([
        fetch('/api/risk/limits', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            stop_loss_pct: data.stopLoss,
            take_profit_1_pct: data.takeProfit,
            daily_loss_limit_pct: data.dailyLossLimit,
            max_concurrent_positions: data.maxSymbols,
          }),
        }),
        fetch('/api/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            trading_mode: data.mode.toLowerCase(),
            telegram_chat_id: data.telegramChatId || '',
            notify_on_order: data.notifyOnOrder,
            notify_on_close: data.notifyOnClose,
            notify_on_stop_loss: data.notifyOnStopLoss,
          }),
        }),
      ]);

      if (!riskRes.ok || !configRes.ok) throw new Error('저장 실패');

      setSaveStatus('success');
      setSaveMessage('설정이 저장되었습니다.');
      setTimeout(() => setSaveStatus('idle'), 3000);
    } catch {
      setSaveStatus('error');
      setSaveMessage('설정 저장에 실패했습니다. 백엔드 연결을 확인하세요.');
    }
  };

  if (pageLoading) {
    return (
      <div className="min-h-screen bg-slate-900 p-8">
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-slate-50">설정</h1>
        </div>
        <div className="space-y-4">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-40 w-full rounded-lg" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">설정</h1>
        <p className="mt-2 text-slate-400">매매 파라미터 및 알림 설정 관리</p>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        {/* 리스크 파라미터 */}
        <Card className="border-slate-700 bg-slate-800">
          <CardHeader>
            <CardTitle className="text-slate-200">리스크 파라미터</CardTitle>
            <CardDescription className="text-slate-500">매매 시 적용되는 손절/익절 설정</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-300">손절 (%)</label>
                <div className="flex items-center gap-3">
                  <Input
                    type="number"
                    step="0.1"
                    {...register('stopLoss', { valueAsNumber: true })}
                    className="border-slate-600 bg-slate-700 text-slate-200"
                  />
                  <span className="whitespace-nowrap text-sm text-slate-500">기본값: 5.0%</span>
                </div>
                {errors.stopLoss && <p className="text-sm text-red-400">⚠ {errors.stopLoss.message}</p>}
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-300">익절 (%)</label>
                <div className="flex items-center gap-3">
                  <Input
                    type="number"
                    step="0.1"
                    {...register('takeProfit', { valueAsNumber: true })}
                    className="border-slate-600 bg-slate-700 text-slate-200"
                  />
                  <span className="whitespace-nowrap text-sm text-slate-500">기본값: 10.0%</span>
                </div>
                {errors.takeProfit && <p className="text-sm text-red-400">⚠ {errors.takeProfit.message}</p>}
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-300">일일 손실 한도 (%)</label>
                <div className="flex items-center gap-3">
                  <Input
                    type="number"
                    step="0.1"
                    {...register('dailyLossLimit', { valueAsNumber: true })}
                    className="border-slate-600 bg-slate-700 text-slate-200"
                  />
                  <span className="whitespace-nowrap text-sm text-slate-500">기본값: 3.0%</span>
                </div>
                {errors.dailyLossLimit && <p className="text-sm text-red-400">⚠ {errors.dailyLossLimit.message}</p>}
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-300">최대 동시 포지션</label>
                <div className="flex items-center gap-3">
                  <Input
                    type="number"
                    step="1"
                    {...register('maxSymbols', { valueAsNumber: true })}
                    className="border-slate-600 bg-slate-700 text-slate-200"
                  />
                  <span className="whitespace-nowrap text-sm text-slate-500">기본값: 5개</span>
                </div>
                {errors.maxSymbols && <p className="text-sm text-red-400">⚠ {errors.maxSymbols.message}</p>}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 매매 모드 */}
        <Card className="border-slate-700 bg-slate-800">
          <CardHeader>
            <CardTitle className="text-slate-200">매매 모드</CardTitle>
            <CardDescription className="text-slate-500">실시간 거래 또는 시뮬레이션 모드 선택</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {(['SIMULATION', 'LIVE'] as const).map((val) => (
              <label
                key={val}
                className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-700 p-3 hover:bg-slate-700 hover:bg-opacity-50"
              >
                <input type="radio" value={val} {...register('mode')} className="h-4 w-4" />
                <div>
                  <div className="font-medium text-slate-200">
                    {val === 'SIMULATION' ? 'Simulation (테스트)' : 'Live (실시간)'}
                  </div>
                  <p className="text-sm text-slate-500">
                    {val === 'SIMULATION' ? '실제 자금 없이 매매 시뮬레이션' : '실시간 거래 활성화'}
                  </p>
                </div>
              </label>
            ))}
            {errors.mode && <p className="text-sm text-red-400">⚠ {errors.mode.message}</p>}
          </CardContent>
        </Card>

        {/* 알림 설정 */}
        <Card className="border-slate-700 bg-slate-800">
          <CardHeader>
            <CardTitle className="text-slate-200">알림 설정</CardTitle>
            <CardDescription className="text-slate-500">텔레그램 알림 환경 설정</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="space-y-2">
              <label className="text-sm font-medium text-slate-300">텔레그램 Chat ID</label>
              <Input
                type="text"
                placeholder="Chat ID 입력 (예: 1234567890)"
                {...register('telegramChatId')}
                className="max-w-xs border-slate-600 bg-slate-700 text-slate-200 placeholder-slate-500"
              />
              <p className="text-xs text-slate-500">텔레그램 봇에서 Chat ID를 확인하세요.</p>
            </div>

            <div className="space-y-3">
              <label className="text-sm font-medium text-slate-300">알림 종류</label>
              {(
                [
                  { key: 'notifyOnOrder', label: '주문 실행 알림' },
                  { key: 'notifyOnClose', label: '포지션 청산 알림' },
                  { key: 'notifyOnStopLoss', label: '손절 알림' },
                ] as const
              ).map(({ key, label }) => (
                <label key={key} className="flex cursor-pointer items-center gap-3 p-2">
                  <input type="checkbox" {...register(key)} className="h-4 w-4 rounded" />
                  <span className="text-sm text-slate-300">{label}</span>
                </label>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* 저장 상태 */}
        {saveStatus !== 'idle' && (
          <div className={`flex items-center gap-3 rounded-lg border p-4 ${
            saveStatus === 'success'
              ? 'border-green-700 bg-green-900 bg-opacity-30 text-green-300'
              : saveStatus === 'error'
              ? 'border-red-700 bg-red-900 bg-opacity-30 text-red-300'
              : 'border-blue-700 bg-blue-900 bg-opacity-30 text-blue-300'
          }`}>
            <span>{saveMessage || '저장 중...'}</span>
          </div>
        )}

        <div className="flex gap-3">
          <Button
            type="submit"
            disabled={saveStatus === 'saving'}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50"
          >
            {saveStatus === 'saving' ? '저장 중...' : '저장하기'}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => { reset(DEFAULT_SETTINGS); setSaveStatus('idle'); }}
            disabled={saveStatus === 'saving'}
            className="border-slate-600 text-slate-300 hover:bg-slate-700"
          >
            초기화
          </Button>
        </div>
      </form>
    </div>
  );
}
