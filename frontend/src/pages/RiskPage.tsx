import React, { useState, useEffect } from 'react';
import api, { ApiError } from '../lib/api';

interface RiskMetrics {
  total_exposure: number;
  margin_used: number;
  margin_available: number;
  max_drawdown: number;
  var_1d: number;
  position_count: number;
}

interface RiskLimits {
  max_order_size: number;
  max_position_size: number;
  max_daily_loss: number;
  max_positions: number;
}

const DEFAULT_LIMITS: RiskLimits = {
  max_order_size: 10000,
  max_position_size: 50000,
  max_daily_loss: 5000,
  max_positions: 10,
};

function num(value: unknown, fallback = 0): number {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function money(value: unknown, digits = 2): string {
  return num(value).toFixed(digits);
}

function normalizeMetrics(raw: Record<string, unknown> | null | undefined): RiskMetrics {
  const data = raw ?? {};
  return {
    total_exposure: num(data.total_exposure),
    // Backend may not expose margin fields yet — default to 0
    margin_used: num(data.margin_used),
    margin_available: num(data.margin_available),
    max_drawdown: num(data.max_drawdown),
    var_1d: num(data.var_1d ?? data.var_95),
    position_count: num(data.position_count ?? data.open_positions),
  };
}

function normalizeLimits(raw: Record<string, unknown> | null | undefined): RiskLimits {
  const data = raw ?? {};
  return {
    max_order_size: num(data.max_order_size, DEFAULT_LIMITS.max_order_size),
    max_position_size: num(data.max_position_size, DEFAULT_LIMITS.max_position_size),
    max_daily_loss: num(data.max_daily_loss, DEFAULT_LIMITS.max_daily_loss),
    max_positions: num(data.max_positions, DEFAULT_LIMITS.max_positions),
  };
}

export default function RiskPage() {
  const [metrics, setMetrics] = useState<RiskMetrics | null>(null);
  const [limits, setLimits] = useState<RiskLimits | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [editingLimits, setEditingLimits] = useState(false);
  const [newLimits, setNewLimits] = useState<RiskLimits>(DEFAULT_LIMITS);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      const [metricsData, limitsData] = await Promise.all([
        api.get<Record<string, unknown>>('/api/risk/metrics'),
        api.get<Record<string, unknown>>('/api/risk/limits'),
      ]);
      const nextMetrics = normalizeMetrics(metricsData);
      const nextLimits = normalizeLimits(limitsData);
      setMetrics(nextMetrics);
      setLimits(nextLimits);
      setNewLimits(nextLimits);
      setFetchError(null);
    } catch (error) {
      const detail =
        error instanceof ApiError
          ? (typeof error.detail === 'string' ? error.detail : error.message)
          : error instanceof Error
            ? error.message
            : 'Failed to load risk data';
      setFetchError(detail);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateLimits = async () => {
    try {
      await api.post('/api/risk/limits', newLimits);
      setEditingLimits(false);
      fetchData();
    } catch (error) {
      const detail =
        error instanceof ApiError
          ? (typeof error.detail === 'string' ? error.detail : error.message)
          : error instanceof Error
            ? error.message
            : 'Failed to update limits';
      setFetchError(detail);
    }
  };

  const getExposurePercentage = () => {
    if (!metrics || !limits || limits.max_position_size <= 0) return 0;
    return (metrics.total_exposure / limits.max_position_size) * 100;
  };

  const getMarginPercentage = () => {
    if (!metrics) return 0;
    const total = metrics.margin_used + metrics.margin_available;
    return total > 0 ? (metrics.margin_used / total) * 100 : 0;
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-8">
        <div className="text-center">Loading risk data...</div>
      </div>
    );
  }

  const m = metrics ?? normalizeMetrics(null);
  const l = limits ?? DEFAULT_LIMITS;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-4xl font-bold text-gray-900 mb-2">🛡️ Risk Management</h1>
            <p className="text-gray-600">Monitor and control trading risk</p>
          </div>
          <button
            onClick={() => (window.location.href = '/trader')}
            className="px-6 py-3 bg-white border-2 border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-all font-semibold"
          >
            ← Back to Dashboard
          </button>
        </div>

        {fetchError && (
          <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {fetchError}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500">Total Exposure</div>
              <div className="text-2xl">💰</div>
            </div>
            <div className="text-3xl font-bold text-gray-900 mb-2">
              ${money(m.total_exposure)}
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all"
                style={{ width: `${Math.min(getExposurePercentage(), 100)}%` }}
              />
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {money(getExposurePercentage(), 1)}% of max position size
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500">Margin Usage</div>
              <div className="text-2xl">📊</div>
            </div>
            <div className="text-3xl font-bold text-gray-900 mb-2">
              ${money(m.margin_used)}
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${
                  getMarginPercentage() > 80
                    ? 'bg-red-600'
                    : getMarginPercentage() > 60
                      ? 'bg-yellow-600'
                      : 'bg-green-600'
                }`}
                style={{ width: `${Math.min(getMarginPercentage(), 100)}%` }}
              />
            </div>
            <div className="text-xs text-gray-500 mt-1">
              ${money(m.margin_available)} available
              {(m.margin_used === 0 && m.margin_available === 0) && (
                <span className="ml-1 text-gray-400">(not provided by API yet)</span>
              )}
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500">Max Drawdown</div>
              <div className="text-2xl">📉</div>
            </div>
            <div className="text-3xl font-bold text-red-600 mb-2">
              {money(m.max_drawdown)}%
            </div>
            <div className="text-xs text-gray-500 mt-1">
              VaR (1D): ${money(m.var_1d)} · Open positions: {m.position_count}
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl shadow-lg p-8 mb-8">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-2xl font-bold text-gray-900">Risk Limits</h2>
            {!editingLimits ? (
              <button
                onClick={() => setEditingLimits(true)}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all font-semibold"
              >
                ✏️ Edit Limits
              </button>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setEditingLimits(false);
                    setNewLimits(l);
                  }}
                  className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-all font-semibold"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUpdateLimits}
                  className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-all font-semibold"
                >
                  💾 Save
                </button>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Order Size ($)
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_order_size}
                  onChange={(e) =>
                    setNewLimits({ ...newLimits, max_order_size: num(e.target.value, 0) })
                  }
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">${money(l.max_order_size)}</div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Position Size ($)
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_position_size}
                  onChange={(e) =>
                    setNewLimits({ ...newLimits, max_position_size: num(e.target.value, 0) })
                  }
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">
                  ${money(l.max_position_size)}
                </div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Daily Loss ($)
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_daily_loss}
                  onChange={(e) =>
                    setNewLimits({ ...newLimits, max_daily_loss: num(e.target.value, 0) })
                  }
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">${money(l.max_daily_loss)}</div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Open Positions
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_positions}
                  onChange={(e) =>
                    setNewLimits({ ...newLimits, max_positions: num(e.target.value, 0) })
                  }
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">{l.max_positions}</div>
              )}
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl shadow-lg p-8">
          <h2 className="text-2xl font-bold text-gray-900 mb-6">Risk Alerts</h2>

          <div className="space-y-4">
            {getMarginPercentage() > 80 && (
              <div className="bg-red-50 border-l-4 border-red-600 p-4 rounded">
                <div className="flex items-center">
                  <div className="text-2xl mr-3">⚠️</div>
                  <div>
                    <div className="font-bold text-red-900">High Margin Usage</div>
                    <div className="text-sm text-red-700">
                      Margin usage is above 80%. Consider reducing exposure.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {m.position_count >= l.max_positions && (
              <div className="bg-yellow-50 border-l-4 border-yellow-600 p-4 rounded">
                <div className="flex items-center">
                  <div className="text-2xl mr-3">⚠️</div>
                  <div>
                    <div className="font-bold text-yellow-900">Max Positions Reached</div>
                    <div className="text-sm text-yellow-700">
                      You have reached the maximum number of open positions.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {getExposurePercentage() > 90 && (
              <div className="bg-red-50 border-l-4 border-red-600 p-4 rounded">
                <div className="flex items-center">
                  <div className="text-2xl mr-3">🚨</div>
                  <div>
                    <div className="font-bold text-red-900">Critical Exposure Level</div>
                    <div className="text-sm text-red-700">
                      Total exposure is above 90% of maximum. Immediate action required.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {getMarginPercentage() < 50 && getExposurePercentage() < 50 && (
              <div className="bg-green-50 border-l-4 border-green-600 p-4 rounded">
                <div className="flex items-center">
                  <div className="text-2xl mr-3">✅</div>
                  <div>
                    <div className="font-bold text-green-900">All Systems Normal</div>
                    <div className="text-sm text-green-700">
                      Risk metrics are within acceptable ranges.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
