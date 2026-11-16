
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useNotification } from "@/contexts/NotificationContext";
import { ApiError } from "@/lib/api";
import { useEffect, useState } from "react";
import nautilusService, { type EngineInfo } from "@/services/nautilusService";

export default function AdminDashboard() {
  const { success, error, info } = useNotification();
  const [engineInfo, setEngineInfo] = useState<EngineInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [initializing, setInitializing] = useState(false);

  useEffect(() => {
    loadEngineInfo();
  }, []);

  const loadEngineInfo = async () => {
    try {
      setLoading(true);
      const data = await nautilusService.getEngineInfo();
      setEngineInfo(data);
    } catch (err) {
      error('Failed to connect to Nautilus API');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleInitialize = async () => {
    setInitializing(true);
    info('正在初始化引擎并加载数据目录…');
    try {
      const result = await nautilusService.initialize();
      const parts = [result.message || '引擎初始化成功'];
      if (typeof result.instruments_count === 'number') {
        parts.push(`${result.instruments_count} instruments`);
      }
      if (result.catalog_path) {
        parts.push(result.catalog_path);
      }
      success(parts.join(' · '));
      await loadEngineInfo();
    } catch (err) {
      const detail =
        err instanceof ApiError
          ? (typeof err.detail === 'string' ? err.detail : err.message)
          : err instanceof Error
            ? err.message
            : '初始化失败';
      error(detail);
      console.error(err);
    } finally {
      setInitializing(false);
    }
  };

  const handleTestNotification = () => {
    success('Admin panel is working! This is a test notification.');
  };

  const handleRefresh = () => {
    info('Refreshing engine info...');
    loadEngineInfo();
  };

  const initialized = Boolean(engineInfo?.is_initialized);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Nautilus Admin Panel</h1>
              <p className="text-sm text-gray-600 mt-1">
                {loading
                  ? '⏳ Connecting...'
                  : engineInfo
                    ? `✅ Connected: ${engineInfo.trader_id} · 引擎: ${initialized ? '已初始化' : '未初始化'}`
                    : '❌ Disconnected'}
              </p>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleRefresh} disabled={loading || initializing}>
                {loading ? 'Loading...' : '🔄 Refresh'}
              </Button>
              <Button variant="outline" onClick={() => window.location.href = '/'}>
                ← Back to Home
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-8">
        <div className="mb-8">
          <h2 className="text-3xl font-bold text-gray-900 mb-2">Dashboard</h2>
          <p className="text-gray-600">Welcome to Nautilus Trader Admin Interface</p>
        </div>

        {/* Engine initialize — required before Real Data backtests */}
        <Card className="mb-8 border-blue-200">
          <CardHeader>
            <CardTitle>🚀 引擎控制</CardTitle>
            <CardDescription>
              真实数据回测前需先初始化引擎（加载 catalog）。演示 / Sweep 不依赖此步骤。
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col sm:flex-row sm:items-center gap-4">
            <div className="flex-1 text-sm text-gray-700">
              <p>
                状态：{' '}
                <span className={initialized ? 'text-green-600 font-medium' : 'text-amber-600 font-medium'}>
                  {loading ? '查询中…' : initialized ? '已初始化' : '未初始化'}
                </span>
              </p>
              {engineInfo?.catalog_path && (
                <p className="text-gray-500 mt-1 break-all">catalog: {engineInfo.catalog_path}</p>
              )}
            </div>
            <Button onClick={handleInitialize} disabled={initializing || loading}>
              {initializing ? '初始化中…' : '初始化引擎'}
            </Button>
          </CardContent>
        </Card>

        {/* Stats Grid */}
        <div className="grid md:grid-cols-4 gap-6 mb-8">
          <Card>
            <CardHeader className="pb-3">
              <CardDescription>Total Operations</CardDescription>
              <CardTitle className="text-3xl">140+</CardTitle>
            </CardHeader>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardDescription>Admin Pages</CardDescription>
              <CardTitle className="text-3xl">8</CardTitle>
            </CardHeader>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardDescription>API Endpoints</CardDescription>
              <CardTitle className="text-3xl">15+</CardTitle>
            </CardHeader>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardDescription>Engine</CardDescription>
              <CardTitle className={`text-3xl ${initialized ? 'text-green-600' : 'text-amber-600'}`}>
                {initialized ? 'Ready' : 'Idle'}
              </CardTitle>
            </CardHeader>
          </Card>
        </div>

        {/* Feature Cards */}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          <Card>
            <CardHeader>
              <CardTitle>💾 Database</CardTitle>
              <CardDescription>PostgreSQL, Parquet, Redis management</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Manage database operations, backups, and cache optimization.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/database'}>
                Open Database
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>🔧 Components</CardTitle>
              <CardDescription>Manage Nautilus components</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Control lifecycle of engines, adapters, and services.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/components'}>
                Open Components
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>🎛️ Features</CardTitle>
              <CardDescription>Feature flags and configuration</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Toggle features and configure system parameters.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/features'}>
                Open Features
              </Button>
            </CardContent>
          </Card>

<Card>
            <CardHeader>
              <CardTitle>🔌 Adapters</CardTitle>
              <CardDescription>Exchange/broker connections</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Manage connections to exchanges and brokers.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/adapters'}>
                Open Adapters
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>📊 Monitoring</CardTitle>
              <CardDescription>System metrics and logs</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                View real-time metrics, logs, and alerts.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/monitoring'}>
                Open Monitoring
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>⚙️ Settings</CardTitle>
              <CardDescription>System configuration</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Configure system settings and preferences.
              </p>
              <Button onClick={() => window.location.href = '/admin/settings'}>
                Settings
              </Button>
            </CardContent>
          </Card>

          {/* API Configuration Card */}
          <Card>
            <CardHeader>
              <CardTitle>🔌 API Configuration</CardTitle>
              <CardDescription>Backend API management</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Configure and test backend API endpoints.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/api-config'}>
                Manage APIs
              </Button>
            </CardContent>
          </Card>

          {/* Database Management Card */}
          <Card>
            <CardHeader>
              <CardTitle>💾 Database Management</CardTitle>
              <CardDescription>Comprehensive database administration</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Manage database connections, execute queries, and create backups.
              </p>
              <Button onClick={() => window.location.href = '/admin/db-management'}>
                Manage Databases
              </Button>
            </CardContent>
          </Card>

{/* Positions Card */}
          <Card>
            <CardHeader>
              <CardTitle>💼 Positions</CardTitle>
              <CardDescription>Position tracking</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Monitor open positions and P&L.
              </p>
              <Button className="w-full" onClick={() => window.location.href = '/admin/positions'}>
                Open Positions
              </Button>
            </CardContent>
          </Card>

{/* User Management Card */}
          <Card>
            <CardHeader>
              <CardTitle>👥 User Management</CardTitle>
              <CardDescription>Manage user accounts and roles</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Create, deactivate, and manage user accounts and access roles.
              </p>
              <Button onClick={() => window.location.href = '/admin/users'}>
                Manage Users
              </Button>
            </CardContent>
          </Card>

{/* Documentation Card */}
          <Card>
            <CardHeader>
              <CardTitle>📚 Documentation</CardTitle>
              <CardDescription>View project documentation</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">
                Read the documentation for the Nautilus Web Interface.
              </p>
              <Button onClick={() => window.location.href = '/docs'}>
                View Documentation
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Test Notification */}
        <div className="mt-8">
          <Card>
            <CardHeader>
              <CardTitle>🔔 Notification System</CardTitle>
              <CardDescription>Test the notification system</CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={handleTestNotification}>
                Test Notification
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Info */}
        <div className="mt-8 text-center text-sm text-gray-500">
          <p>Nautilus Web Interface v1.0.0</p>
          <p className="mt-2">
            <a href="https://github.com/Black101081/Nautilus-Web-Interface" className="text-blue-600 hover:underline">
              View on GitHub
            </a>
          </p>
        </div>
      </main>
    </div>
  );
}

