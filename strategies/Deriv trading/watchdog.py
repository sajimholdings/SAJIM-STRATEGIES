"""
watchdog.py — Tier 2 AI Engine for V3
Uses HMM for regime detection and GARCH(1,1) for volatility forecasting.
Calculates barrier distances and expected values, then pushes to Node.js.
"""

import numpy as np
from sklearn.mixture import GaussianMixture
import json
import requests
from datetime import datetime
import time
import os

class HMMRegimeDetector:
    def __init__(self, assets=['1HZ75V', '1HZ50V']):
        self.assets = assets
        self.models = {}
        
        # Initialize GaussianMixture for each asset instead of hmmlearn
        # due to C++ dependency issues on Windows
        for asset in assets:
            self.models[asset] = GaussianMixture(
                n_components=4,
                covariance_type='diag',
                random_state=42,
                max_iter=100
            )
    
    def calculate_features(self, prices):
        """Calculate log returns and rolling variance."""
        # Convert prices to numpy array if it isn't already
        prices = np.array(prices)
        
        # Add a tiny epsilon to prevent log(0) if any price is somehow 0
        returns = np.diff(np.log(prices + 1e-8))
        
        # Rolling variance (10-tick window)
        rolling_var = []
        window_size = 10
        for i in range(len(returns)):
            if i < window_size:
                var = np.var(returns[:i+1])
            else:
                var = np.var(returns[i-window_size:i])
            rolling_var.append(var)
            
        rolling_var = np.array(rolling_var)
        
        # Feature matrix: [returns, rolling_variance]
        X = np.column_stack([returns, rolling_var])
        return X, returns, rolling_var
    
    def fit_and_predict(self, asset, prices, lookback=100):
        """Fit HMM and predict the current regime using asset-specific lookback."""
        if len(prices) < lookback:
            return {'status': 'NEUTRAL', 'confidence': 0}
        
        recent_prices = prices[-lookback:]
        X, returns, rolling_var = self.calculate_features(recent_prices)
        
        try:
            # Fit the model
            self.models[asset].fit(X)
            
            # Predict hidden states (Viterbi path)
            hidden_states = self.models[asset].predict(X)
            
            # The current state is the last one in the sequence
            current_state = hidden_states[-1]
            
            # Characterize all states to understand what they represent
            state_chars = self._characterize_states(hidden_states, returns, rolling_var)
            
            # Map the abstract states (0, 1, 2, 3) to semantic regimes
            regime_map = self._map_states_to_regimes(state_chars)
            current_regime = regime_map[current_state]
            
            # Calculate a confidence score based on the model's log-likelihood
            log_score = self.models[asset].score(X)
            confidence = min(0.95, max(0.5, (log_score + 1000) / 1500))
            
            # [DEBUG] diagnostic print to track fit freshness
            print(f"[DEBUG] {asset} | log_score: {log_score:.6f} | n_samples: {len(X)} | regime: {current_regime}")
            print(f"[DEBUG] {asset} | first tick: {recent_prices[0]} | last tick: {recent_prices[-1]}")
            
            return {
                'status': current_regime,
                'confidence': confidence,
                'current_state': int(current_state),
            }
            
        except Exception as e:
            print(f"Error fitting HMM for {asset}: {e}")
            return {'status': 'NEUTRAL', 'confidence': 0}
            
    def _characterize_states(self, hidden_states, returns, rolling_var):
        """Describe the statistical properties of each HMM state."""
        state_chars = {}
        for i in range(4):
            mask = hidden_states == i
            if np.sum(mask) > 0:
                state_chars[i] = {
                    'mean_return': np.mean(returns[mask]),
                    'mean_variance': np.mean(rolling_var[mask]),
                    'prevalence': np.sum(mask) / len(mask),
                    'std_return': np.std(returns[mask])
                }
            else:
                state_chars[i] = {
                    'mean_return': 0,
                    'mean_variance': 0,
                    'prevalence': 0,
                    'std_return': 0
                }
        return state_chars
        
    def _map_states_to_regimes(self, state_chars):
        """Map abstract states to semantic regime names based on variance."""
        # Rank states primarily by variance, then by absolute return
        ranked = sorted(
            state_chars.items(),
            key=lambda x: (x[1]['mean_variance'], -abs(x[1]['mean_return']))
        )
        
        # Lowest variance -> COMPRESSION
        # Moderate variance -> CHOP
        # Higher variance, directional -> TREND
        # Highest variance -> SPIKE / FAKEOUT
        regime_map = {
            ranked[0][0]: 'COMPRESSION',
            ranked[1][0]: 'CHOP',
            ranked[2][0]: 'TREND',
            ranked[3][0]: 'SPIKE'
        }
        return regime_map
        
    def calculate_garch_forecast(self, returns):
        """Simple GARCH(1,1) forward volatility forecast."""
        # Simplified parameters
        omega = 0.00001
        alpha = 0.05
        beta = 0.94
        
        long_var = np.var(returns)
        if long_var == 0:
            return {'forecast_variance': 0, 'multiplier': 1.0, 'category': 'MODERATE'}
            
        cond_var = long_var
        # Fit over the last 20 returns
        for r in returns[-20:]:
            cond_var = omega + alpha * (r ** 2) + beta * cond_var
            
        # Forecast 60 steps (ticks) ahead
        forecast_var = cond_var
        for _ in range(60):
            forecast_var = omega + alpha * forecast_var + beta * forecast_var
            
        multiplier = forecast_var / long_var
        
        if multiplier > 1.5:
            category = 'HIGH'
        elif multiplier < 0.7:
            category = 'LOW'
        else:
            category = 'MODERATE'
            
        return {
            'forecast_variance': forecast_var,
            'multiplier': multiplier,
            'category': category
        }
        
    def calculate_barrier_distance(self, asset_regime):
        """Determine optimal barrier distance (pips) and Kelly multiplier."""
        regime = asset_regime['status']
        confidence = asset_regime['confidence']
        garch = asset_regime.get('garch', {})
        
        # Base distances are asset-agnostic here. 
        # (The Node engine will normalize this distance using actual price levels later)
        base_distance = 2.0 
        
        regime_multipliers = {
            'COMPRESSION': 0.8,
            'TREND': 1.2,
            'CHOP': 1.8,
            'SPIKE': 3.0,
            'NEUTRAL': 1.5
        }
        
        multiplier = regime_multipliers.get(regime, 1.5)
        
        # Adjust based on GARCH forecast
        garch_multiplier = garch.get('multiplier', 1.0)
        if garch_multiplier > 1.5:
            multiplier *= 1.3
        elif garch_multiplier < 0.7:
            multiplier *= 0.8
            
        barrier_distance = base_distance * multiplier
        
        # Calculate Fractional Kelly multiplier
        kelly_multiplier = min(0.35, max(0.15, 0.25 * confidence))
        
        return {
            'barrier_distance_pips': round(barrier_distance, 2),
            'kelly_multiplier': round(kelly_multiplier, 3)
        }
        
    def run_cycle(self, tick_data_dict):
        """Run a complete analysis cycle with asset-specific lookback windows."""
        results = {}
        for asset, prices in tick_data_dict.items():
            if asset not in self.assets or not prices:
                continue
                
            # Get asset-specific lookback window
            if asset == '1HZ10V':
                lookback = 100
            elif asset == '1HZ50V':
                lookback = 60
            elif asset == '1HZ75V':
                lookback = 35
            else:
                lookback = 60
                
            # Regime classification
            regime = self.fit_and_predict(asset, prices, lookback)
            
            # Volatility forecasting on asset-specific sliced returns
            recent_prices = prices[-lookback:]
            _, returns, _ = self.calculate_features(recent_prices)
            regime['garch'] = self.calculate_garch_forecast(returns)
            
            # Barrier / risk sizing
            barrier_params = self.calculate_barrier_distance(regime)
            
            results[asset] = {
                'regime': regime,
                'barrier_params': barrier_params
            }
        return results

# ─────────────────────────────────────────────────────────────────────────────
# MAIN EVENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def get_placeholder_ticks(count):
    """Placeholder: simulate recent price history via random walk."""
    prices = [100.0]
    for _ in range(count - 1):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.0001)))
    return prices

def fetch_live_ticks(app_id, api_token, asset, count=1000):
    """Retrieve actual live tick history from the Node.js Express server cache."""
    try:
        resp = requests.get(
            f"http://127.0.0.1:3001/api/ticks?asset={asset}&count={count}",
            timeout=5
        )
        if resp.status_code == 200:
            ticks = resp.json().get('ticks', [])
            if len(ticks) >= 100:
                return ticks
    except Exception as e:
        print(f"Error fetching live ticks from Node.js for {asset}: {e}")
        
    return get_placeholder_ticks(count)

def main():
    print("Starting V3 AI Watchdog (HMM + GARCH)...")
    detector = HMMRegimeDetector()
    
    # Environment config
    app_id = os.environ.get('DERIV_APP_ID', '1089')
    api_token = os.environ.get('DERIV_API_TOKEN', '')
    
    while True:
        try:
            # 1. Fetch ticks
            tick_data = {
                '1HZ75V': fetch_live_ticks(app_id, api_token, '1HZ75V', 1000),
                '1HZ50V': fetch_live_ticks(app_id, api_token, '1HZ50V', 1000)
            }
            
            # 2. Run analysis
            results = detector.run_cycle(tick_data)
            
            # 3. Push to Node.js
            for asset, data in results.items():
                payload = {
                    'asset': asset,
                    'regime': data['regime']['status'],
                    'confidence': float(data['regime']['confidence']),
                    'barrier_distance_pips': data['barrier_params']['barrier_distance_pips'],
                    'kelly_multiplier': data['barrier_params']['kelly_multiplier'],
                    'garch_forecast': data['regime']['garch']['category'],
                    'timestamp': datetime.now().isoformat()
                }
                
                try:
                    # Send via webhook
                    resp = requests.post(
                        'http://127.0.0.1:3001/api/regime-update',
                        json=payload,
                        timeout=5
                    )
                    if resp.status_code == 200:
                        print(f"[{asset}] Sent regime update: {payload['regime']} | Barrier: {payload['barrier_distance_pips']}p")
                except requests.exceptions.RequestException as e:
                    print(f"Error communicating with Node.js bridge for {asset}: {e}")
                    
        except Exception as e:
            print(f"Cycle error: {e}")
            
        # Sleep for 60 seconds (1 evaluation per minute)
        time.sleep(60)

if __name__ == '__main__':
    main()
