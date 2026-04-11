'use client';

import { useState, useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

// Zod Schema for validation
const SettingsSchema = z.object({
  stopLoss: z.number().min(0.1, '0.1 이상이어야 합니다').max(50, '50 이하여야 합니다'),
  takeProfit: z.number().min(0.1, '0.1 이상이어야 합니다').max(100, '100 이하여야 합니다'),
  dailyLimit: z.number().min(100, '$100 이상이어야 합니다').max(1000000, '$1,000,000 이하여야 합니다'),
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

interface Settings {
  stopLoss: number;
  takeProfit: number;
  dailyLimit: number;
  maxSymbols: number;
  mode: 'SIMULATION' | 'LIVE';
  telegramChatId?: string;
  notifyOnOrder: boolean;
  notifyOnClose: boolean;
  notifyOnStopLoss: boolean;
}

// Mock data
const MOCK_SETTINGS: Settings = {
  stopLoss: 5.0,
  takeProfit: 10.0,
  dailyLimit: 5000,
  maxSymbols: 5,
  mode: 'SIMULATION',
  telegramChatId: '1234567890',
  notifyOnOrder: true,
  notifyOnClose: true,
  notifyOnStopLoss: true,
};

export default function SettingsPage() {
  const [isLoading, setIsLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle');
  const [saveMessage, setSaveMessage] = useState('');

  const {
    register,
    handleSubmit,
    reset,
    watch,
    formState: { errors },
  } = useForm<SettingsFormData>({
    resolver: zodResolver(SettingsSchema),
    defaultValues: MOCK_SETTINGS,
  });

  const mode = watch('mode');
  const telegramChatId = watch('telegramChatId');

  // 초기 데이터 로드
  useEffect(() => {
    const loadSettings = async () => {
      try {
        // TODO: API 엔드포인트 (현재 Mock data)
        // const response = await fetch('/api/settings');
        // const data = await response.json();
        reset(MOCK_SETTINGS);
      } catch (error) {
        console.error('Failed to load settings:', error);
        reset(MOCK_SETTINGS);
      } finally {
        setIsLoading(false);
      }
    };

    loadSettings();
  }, [reset]);

  const onSubmit = async (data: SettingsFormData) => {
    try {
      setSaveStatus('saving');
      setSaveMessage('');

      // TODO: API 엔드포인트 통합
      // const response = await fetch('/api/config', {
      //   method: 'PUT',
      //   headers: { 'Content-Type': 'application/json' },
      //   body: JSON.stringify(data),
      // });

      // Mock: 설정 저장 시뮬레이션
      await new Promise((resolve) => setTimeout(resolve, 1000));

      setSaveStatus('success');
      setSaveMessage('설정이 저장되었습니다.');
      setTimeout(() => setSaveStatus('idle'), 3000);
    } catch (error) {
      console.error('Failed to save settings:', error);
      setSaveStatus('error');
      setSaveMessage('설정 저장에 실패했습니다. 다시 시도해주세요.');
    }
  };

  const handleReset = () => {
    reset(MOCK_SETTINGS);
    setSaveStatus('idle');
    setSaveMessage('');
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="text-center">
          <p className="text-muted-foreground">설정을 불러오는 중...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">설정</h1>
        <p className="text-sm text-muted-foreground mt-1">
          매매 파라미터 및 알림 설정 관리
        </p>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        {/* Section 1: Risk Parameters */}
        <Card>
          <CardHeader>
            <CardTitle>리스크 파라미터</CardTitle>
            <CardDescription>매매 시 적용되는 손절/익절 설정</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Stop Loss */}
            <div className="space-y-2">
              <label htmlFor="stopLoss" className="text-sm font-medium">손절(Stop Loss) (%)</label>
              <div className="flex items-center gap-4">
                <Input
                  id="stopLoss"
                  type="number"
                  step="0.1"
                  {...register('stopLoss', { valueAsNumber: true })}
                  className={errors.stopLoss ? 'border-red-500' : ''}
                />
                <span className="text-sm text-slate-400 whitespace-nowrap">
                  기본값: 5.0%
                </span>
              </div>
              {errors.stopLoss && (
                <p className="text-sm text-red-400 flex items-center gap-1">
                  ⚠️ {errors.stopLoss.message}
                </p>
              )}
            </div>

            {/* Take Profit */}
            <div className="space-y-2">
              <label htmlFor="takeProfit" className="text-sm font-medium">익절(Take Profit) (%)</label>
              <div className="flex items-center gap-4">
                <Input
                  id="takeProfit"
                  type="number"
                  step="0.1"
                  {...register('takeProfit', { valueAsNumber: true })}
                  className={errors.takeProfit ? 'border-red-500' : ''}
                />
                <span className="text-sm text-slate-400 whitespace-nowrap">
                  기본값: 10.0%
                </span>
              </div>
              {errors.takeProfit && (
                <p className="text-sm text-red-400 flex items-center gap-1">
                  ⚠️ {errors.takeProfit.message}
                </p>
              )}
            </div>

            {/* Daily Limit */}
            <div className="space-y-2">
              <label htmlFor="dailyLimit" className="text-sm font-medium">일일 한도 (USD)</label>
              <div className="flex items-center gap-4">
                <Input
                  id="dailyLimit"
                  type="number"
                  step="100"
                  {...register('dailyLimit', { valueAsNumber: true })}
                  className={errors.dailyLimit ? 'border-red-500' : ''}
                />
                <span className="text-sm text-slate-400 whitespace-nowrap">
                  기본값: $5,000
                </span>
              </div>
              {errors.dailyLimit && (
                <p className="text-sm text-red-400 flex items-center gap-1">
                  ⚠️ {errors.dailyLimit.message}
                </p>
              )}
            </div>

            {/* Max Symbols */}
            <div className="space-y-2">
              <label htmlFor="maxSymbols" className="text-sm font-medium">최대 종목수</label>
              <div className="flex items-center gap-4">
                <Input
                  id="maxSymbols"
                  type="number"
                  step="1"
                  {...register('maxSymbols', { valueAsNumber: true })}
                  className={errors.maxSymbols ? 'border-red-500' : ''}
                />
                <span className="text-sm text-slate-400 whitespace-nowrap">
                  기본값: 5개
                </span>
              </div>
              {errors.maxSymbols && (
                <p className="text-sm text-red-400 flex items-center gap-1">
                  ⚠️ {errors.maxSymbols.message}
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Section 2: Trading Mode */}
        <Card>
          <CardHeader>
            <CardTitle>매매 모드</CardTitle>
            <CardDescription>실시간 거래 또는 시뮬레이션 모드 선택</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-3">
              <div className="flex items-center space-x-3 p-3 border border-slate-700 rounded-lg cursor-pointer hover:bg-slate-800/50">
                <input
                  type="radio"
                  id="mode-simulation"
                  value="SIMULATION"
                  {...register('mode')}
                  className="w-4 h-4"
                />
                <label htmlFor="mode-simulation" className="flex-1 cursor-pointer">
                  <div className="font-medium">Simulation (테스트)</div>
                  <p className="text-sm text-slate-400">실제 자금 없이 매매 시뮬레이션</p>
                </label>
              </div>

              <div className="flex items-center space-x-3 p-3 border border-slate-700 rounded-lg cursor-pointer hover:bg-slate-800/50">
                <input
                  type="radio"
                  id="mode-live"
                  value="LIVE"
                  {...register('mode')}
                  className="w-4 h-4"
                />
                <label htmlFor="mode-live" className="flex-1 cursor-pointer">
                  <div className="font-medium">Live (실시간)</div>
                  <p className="text-sm text-slate-400">실시간 거래 활성화</p>
                </label>
              </div>
            </div>

            {errors.mode && (
              <p className="text-sm text-red-400 flex items-center gap-1">
                ⚠️ {errors.mode.message}
              </p>
            )}
          </CardContent>
        </Card>

        {/* Section 3: Notifications */}
        <Card>
          <CardHeader>
            <CardTitle>알림 설정</CardTitle>
            <CardDescription>거래 및 시스템 알림 환경 설정</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Telegram Chat ID */}
            <div className="space-y-3">
              <label htmlFor="telegramChatId" className="text-sm font-medium">텔레그램 Chat ID</label>
              <Input
                id="telegramChatId"
                type="text"
                placeholder="Chat ID 입력 (예: 1234567890)"
                {...register('telegramChatId')}
              />
              <p className="text-xs text-slate-400">
                텔레그램 봇으로부터 Chat ID를 얻을 수 있습니다.
              </p>
            </div>

            {/* Notification Types */}
            <div className="space-y-3">
              <label className="text-sm font-medium">알림 종류</label>
              <div className="space-y-3">
                <div className="flex items-center space-x-2 p-2">
                  <input
                    type="checkbox"
                    id="notifyOnOrder"
                    {...register('notifyOnOrder')}
                    className="w-4 h-4"
                  />
                  <label htmlFor="notifyOnOrder" className="font-normal cursor-pointer flex-1">
                    주문 실행 알림
                  </label>
                </div>

                <div className="flex items-center space-x-2 p-2">
                  <input
                    type="checkbox"
                    id="notifyOnClose"
                    {...register('notifyOnClose')}
                    className="w-4 h-4"
                  />
                  <label htmlFor="notifyOnClose" className="font-normal cursor-pointer flex-1">
                    포지션 청산 알림
                  </label>
                </div>

                <div className="flex items-center space-x-2 p-2">
                  <input
                    type="checkbox"
                    id="notifyOnStopLoss"
                    {...register('notifyOnStopLoss')}
                    className="w-4 h-4"
                  />
                  <label htmlFor="notifyOnStopLoss" className="font-normal cursor-pointer flex-1">
                    손절 알림
                  </label>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Save Status Message */}
        {saveStatus !== 'idle' && (
          <div className={`p-4 rounded-lg flex items-center gap-3 ${
            saveStatus === 'success'
              ? 'bg-green-900 text-green-200 border border-green-700'
              : saveStatus === 'error'
              ? 'bg-red-900 text-red-200 border border-red-700'
              : 'bg-blue-900 text-blue-200 border border-blue-700'
          }`}>
            {saveStatus === 'success' && <span>✓</span>}
            {saveStatus === 'error' && <span>✕</span>}
            <span>{saveMessage}</span>
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex gap-3">
          <Button type="submit" disabled={saveStatus === 'saving'}>
            {saveStatus === 'saving' ? '저장 중...' : '저장하기'}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={handleReset}
            disabled={saveStatus === 'saving'}
          >
            초기화
          </Button>
        </div>
      </form>
    </div>
  );
}
