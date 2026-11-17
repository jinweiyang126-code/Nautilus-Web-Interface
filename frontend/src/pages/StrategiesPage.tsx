import React, { useState, useEffect } from 'react';
import api from '../lib/api';

interface LiveStatus {
  running?: boolean;
  exchange?: string;
  instrument?: string;
  quantity?: number;
  bar?: string;
  position?: string;
  last_signal?: string | null;
  last_error?: string | null;
  last_bar_ts?: string | null;
  ticks?: number;
}

interface Strategy {
  id: string;
  name: string;
  type: string;
  status: string;
  description: string;
  instrument?: string;
  trade_size?: string | number;
  config?: Record<string, any>;
  live?: LiveStatus | null;
  performance: {
    total_pnl: number;
    total_trades: number;
    win_rate: number;
  };
}

interface StrategyTypeOption {
  id: string;
  label: string;
  description: string;
  default_config?: Record<string, unknown>;
}

const FALLBACK_STRATEGY_TYPES: StrategyTypeOption[] = [
  {
    id: 'sma_crossover',
    label: 'SMA Crossover',
    description: 'Buy when fast SMA crosses above slow SMA, sell on cross-down.',
  },
  {
    id: 'rsi',
    label: 'RSI Mean-Reversion',
    description: 'Enter long on RSI oversold cross-up, short on overbought cross-down.',
  },
  {
    id: 'macd',
    label: 'MACD Crossover',
    description: 'Buy when MACD line crosses above signal line, sell on cross-down.',
  },
];

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategyTypes, setStrategyTypes] = useState<StrategyTypeOption[]>(FALLBACK_STRATEGY_TYPES);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newStrategy, setNewStrategy] = useState({
    name: '',
    type: 'sma_crossover',
    description: '',
    instrument_id: 'BTC-USDT',
    trade_size: '0.01',
    config: {} as Record<string, unknown>,
  });

  useEffect(() => {
    fetchStrategies();
    fetchStrategyTypes();
  }, []);

  // Poll while any strategy is live-running so signal/position stay fresh
  useEffect(() => {
    const anyLive = strategies.some(s => s.status === 'running' && s.live?.running);
    if (!anyLive) return;
    const timer = setInterval(() => {
      fetchStrategies();
    }, 15000);
    return () => clearInterval(timer);
  }, [strategies]);

  const fetchStrategyTypes = async () => {
    try {
      const data = await api.get<{ types?: StrategyTypeOption[]; strategy_types?: StrategyTypeOption[] }>(
        '/api/strategy-types',
      );
      const list = data.types || data.strategy_types || [];
      if (list.length > 0) {
        setStrategyTypes(list);
        setNewStrategy(prev => ({
          ...prev,
          type: list.some(t => t.id === prev.type) ? prev.type : list[0].id,
        }));
      }
    } catch {
      // Keep fallback types (includes MACD)
    }
  };

  const fetchStrategies = async () => {
    try {
      const data = await api.get<{ strategies: Strategy[] }>('/api/strategies');
      setStrategies(data.strategies || []);
      setFetchError(null);
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Failed to load strategies');
    } finally {
      setLoading(false);
    }
  };

  const handleTypeChange = (typeId: string) => {
    const meta = strategyTypes.find(t => t.id === typeId);
    setNewStrategy(prev => ({
      ...prev,
      type: typeId,
      description: prev.description || meta?.description || '',
      // Keep user instrument / trade_size; only merge indicator defaults
      config: meta?.default_config
        ? {
            ...meta.default_config,
            instrument_id: prev.instrument_id,
            trade_size: prev.trade_size,
          }
        : prev.config,
    }));
  };

  const handleAddStrategy = async () => {
    setModalError(null);
    const size = String(newStrategy.trade_size || '').trim();
    const inst = String(newStrategy.instrument_id || '').trim();
    if (!inst) {
      setModalError('Instrument is required (e.g. BTC-USDT)');
      return;
    }
    if (!size || Number(size) <= 0) {
      setModalError('Trade size must be > 0 (base coin amount from this strategy)');
      return;
    }
    if (Number(size) > 100) {
      setModalError('Trade size looks like an FX lot. For OKX Demo use base size e.g. 0.01 BTC');
      return;
    }
    try {
      const meta = strategyTypes.find(t => t.id === newStrategy.type);
      const defaults = meta?.default_config || {};
      await api.post('/api/strategies', {
        name: newStrategy.name,
        type: newStrategy.type,
        description: newStrategy.description || meta?.description || '',
        ...defaults,
        ...newStrategy.config,
        // Strategy owns live sizing / symbol
        instrument_id: inst,
        trade_size: size,
      });
      setShowAddModal(false);
      setNewStrategy({
        name: '',
        type: strategyTypes[0]?.id || 'sma_crossover',
        description: '',
        instrument_id: 'BTC-USDT',
        trade_size: '0.01',
        config: {},
      });
      fetchStrategies();
    } catch (error) {
      setModalError(error instanceof Error ? error.message : 'Failed to add strategy');
    }
  };

  const handleStartStrategy = async (strategyId: string) => {
    setFetchError(null);
    try {
      // Size + instrument come from strategy config on the server
      await api.post(`/api/strategies/${strategyId}/start`, {
        live: true,
        exchange: 'okx',
        bar: '1m',
        interval_sec: 60,
      });
      fetchStrategies();
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Failed to start strategy');
    }
  };

  const handleStopStrategy = async (strategyId: string) => {
    try {
      await api.post(`/api/strategies/${strategyId}/stop`);
      fetchStrategies();
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Failed to stop strategy');
    }
  };

  const handleDeleteStrategy = async (strategyId: string) => {
    if (!confirm('Are you sure you want to delete this strategy?')) return;
    try {
      await api.delete(`/api/strategies/${strategyId}`);
      fetchStrategies();
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Failed to delete strategy');
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-8">
        <div className="text-center">Loading strategies...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-8">
      <div className="max-w-7xl mx-auto">
        {fetchError && (
          <div className="mb-4 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
            {fetchError}
          </div>
        )}
        {/* Header */}
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-4xl font-bold text-gray-900 mb-2">📈 Strategy Management</h1>
            <p className="text-gray-600">
              Start runs OKX Demo paper auto-trading (connect OKX Demo in Adapters first)
            </p>
          </div>
          <div className="flex gap-4">
            <button
              onClick={() => window.location.href = '/trader'}
              className="px-6 py-3 bg-white border-2 border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-all font-semibold"
            >
              ← Back to Dashboard
            </button>
            <button
              onClick={() => setShowAddModal(true)}
              className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all font-semibold"
            >
              + Add Strategy
            </button>
          </div>
        </div>

        {/* Strategies Grid */}
        {strategies.length === 0 ? (
          <div className="bg-white rounded-xl shadow-lg p-12 text-center">
            <div className="text-6xl mb-4">📊</div>
            <h3 className="text-2xl font-bold text-gray-900 mb-2">No Strategies Yet</h3>
            <p className="text-gray-600 mb-6">Add your first trading strategy to get started</p>
            <button
              onClick={() => setShowAddModal(true)}
              className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all font-semibold"
            >
              + Add Strategy
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {strategies.map((strategy) => (
              <div key={strategy.id} className="bg-white rounded-xl shadow-lg p-6 hover:shadow-xl transition-shadow">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-xl font-bold text-gray-900">{strategy.name}</h3>
                    <p className="text-sm text-gray-500">{strategy.type}</p>
                  </div>
                  <span className={`px-3 py-1 rounded-full text-sm font-semibold ${
                    strategy.status === 'running' 
                      ? 'bg-green-100 text-green-800' 
                      : 'bg-gray-100 text-gray-800'
                  }`}>
                    {strategy.status}
                  </span>
                </div>

                <p className="text-gray-600 text-sm mb-4">{strategy.description || 'No description'}</p>
                <p className="text-xs text-gray-500 mb-4">
                  {strategy.instrument || strategy.config?.instrument_id || '—'}
                  {' · '}
                  size {strategy.trade_size ?? strategy.config?.trade_size ?? '—'}
                </p>

                {/* Performance Metrics */}
                <div className="bg-gray-50 rounded-lg p-4 mb-4">
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <div className="text-xs text-gray-500">P&L</div>
                      <div className={`font-bold ${(strategy.performance?.total_pnl ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        ${(strategy.performance?.total_pnl ?? 0).toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500">Trades</div>
                      <div className="font-bold text-gray-900">{strategy.performance?.total_trades ?? 0}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500">Win Rate</div>
                      <div className="font-bold text-blue-600">{((strategy.performance?.win_rate ?? 0) * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                </div>

                {strategy.live?.running && (
                  <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900 space-y-1">
                    <div className="font-semibold">OKX Demo live</div>
                    <div>
                      {strategy.live.instrument || strategy.instrument || '—'} · {strategy.live.bar || '1m'} · qty{' '}
                      {strategy.live.quantity ?? strategy.trade_size ?? '—'}
                    </div>
                    <div>
                      position: {strategy.live.position || 'flat'} · signal:{' '}
                      {strategy.live.last_signal || '—'} · ticks: {strategy.live.ticks ?? 0}
                    </div>
                    {strategy.live.last_error && (
                      <div className="text-red-700">error: {strategy.live.last_error}</div>
                    )}
                  </div>
                )}

                {/* Actions */}
                <div className="flex gap-2">
                  {strategy.status === 'stopped' ? (
                    <button
                      onClick={() => handleStartStrategy(strategy.id)}
                      className="flex-1 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-all font-semibold text-sm"
                    >
                      ▶ Start OKX Demo
                    </button>
                  ) : (
                    <button
                      onClick={() => handleStopStrategy(strategy.id)}
                      className="flex-1 px-4 py-2 bg-yellow-600 text-white rounded-lg hover:bg-yellow-700 transition-all font-semibold text-sm"
                    >
                      ⏸ Stop
                    </button>
                  )}
                  <button
                    onClick={() => handleDeleteStrategy(strategy.id)}
                    className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-all font-semibold text-sm"
                  >
                    🗑️
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Add Strategy Modal */}
        {showAddModal && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-xl shadow-2xl p-8 max-w-md w-full">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">Add New Strategy</h2>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">Strategy Name</label>
                  <input
                    type="text"
                    value={newStrategy.name}
                    onChange={(e) => setNewStrategy({ ...newStrategy, name: e.target.value })}
                    className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                    placeholder="My Trading Strategy"
                  />
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">Strategy Type</label>
                  <select
                    value={newStrategy.type}
                    onChange={(e) => handleTypeChange(e.target.value)}
                    className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                  >
                    {strategyTypes.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.label || t.id}
                      </option>
                    ))}
                  </select>
                  {strategyTypes.find(t => t.id === newStrategy.type)?.description && (
                    <p className="mt-1 text-xs text-gray-500">
                      {strategyTypes.find(t => t.id === newStrategy.type)?.description}
                    </p>
                  )}
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">Instrument</label>
                  <input
                    type="text"
                    value={newStrategy.instrument_id}
                    onChange={(e) => setNewStrategy({ ...newStrategy, instrument_id: e.target.value })}
                    className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                    placeholder="BTC-USDT"
                  />
                  <p className="mt-1 text-xs text-gray-500">OKX Demo symbol this strategy will trade</p>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">Trade size</label>
                  <input
                    type="text"
                    value={newStrategy.trade_size}
                    onChange={(e) => setNewStrategy({ ...newStrategy, trade_size: e.target.value })}
                    className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                    placeholder="0.01"
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    Base coin amount per signal (e.g. 0.01 BTC). Start uses this — not a fixed default.
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">Description</label>
                  <textarea
                    value={newStrategy.description}
                    onChange={(e) => setNewStrategy({ ...newStrategy, description: e.target.value })}
                    className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                    rows={3}
                    placeholder="Describe your strategy..."
                  />
                </div>
              </div>

              {modalError && (
                <div className="mt-4 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">
                  {modalError}
                </div>
              )}
              <div className="flex gap-4 mt-6">
                <button
                  onClick={() => { setShowAddModal(false); setModalError(null); }}
                  className="flex-1 px-6 py-3 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-all font-semibold"
                >
                  Cancel
                </button>
                <button
                  onClick={handleAddStrategy}
                  className="flex-1 px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all font-semibold"
                  disabled={!newStrategy.name || !newStrategy.instrument_id || !newStrategy.trade_size}
                >
                  Add Strategy
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

