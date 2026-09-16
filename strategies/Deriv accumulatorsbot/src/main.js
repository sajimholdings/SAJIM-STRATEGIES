import './style.css'
import { DerivWS } from './ws.js'
import { TickChart } from './chart.js'
import { DigitAnalyzer } from './analyzer.js'

// ─── Config ────────────────────────────────────────────────────────────────
const API_TOKEN = 'pat_959cd91acbc8b42c7c81dab35bd1943a42bd43052411dcb3777eb931ba3b0399'
const APP_ID = '1089'

// ─── HTML Shell ────────────────────────────────────────────────────────────
document.querySelector('#app').innerHTML = `
  <header class="glass-panel global-header">
    <div class="brand-logo">Flexa Pro Terminal</div>
    <div class="header-controls">
      <select id="market-select">
        <option value="R_10">Volatility 10 Index</option>
        <option value="R_25">Volatility 25 Index</option>
        <option value="R_50">Volatility 50 Index</option>
        <option value="R_75">Volatility 75 Index</option>
        <option value="R_100" selected>Volatility 100 Index</option>
        <option value="1HZ10V">Volatility 10 (1s)</option>
        <option value="1HZ25V">Volatility 25 (1s)</option>
        <option value="1HZ50V">Volatility 50 (1s)</option>
        <option value="1HZ75V">Volatility 75 (1s)</option>
        <option value="1HZ100V">Volatility 100 (1s)</option>
      </select>
      <select id="trade-type-select">
        <option value="DIGITMATCH">Digit Matches</option>
        <option value="DIGITDIFF">Digit Differs</option>
        <option value="DIGITEVEN">Even / Odd</option>
        <option value="DIGITOVER">Over / Under</option>
        <option value="CALL">Rise / Fall</option>
      </select>
      <div class="account-info">
        <span class="account-badge demo">DEMO</span>
        <span class="balance" id="account-balance">— USD</span>
      </div>
      <div class="connection-dot" id="conn-dot" title="Disconnected"></div>
    </div>
  </header>

  <main class="glass-panel chart-container">
    <div class="chart-header">
      <div>
        <div id="chart-title" class="chart-title">Volatility 100 Index</div>
        <div id="live-price" class="live-price">—</div>
      </div>
      <div class="chart-tools">
        <button class="tool-btn active" data-ticks="60">60 Ticks</button>
        <button class="tool-btn" data-ticks="120">120 Ticks</button>
        <button class="tool-btn" data-ticks="300">300 Ticks</button>
      </div>
    </div>
    <canvas id="price-chart"></canvas>
  </main>

  <aside class="glass-panel analyzer-sidebar">
    <div class="analyzer-tabs">
      <div class="tab active" data-tab="digits">Digit Probabilities</div>
      <div class="tab" data-tab="trend">RSI / SMA</div>
    </div>

    <div class="digit-stats" id="digit-stats-container"></div>

    <div class="last-digits-strip" id="last-digits-strip"></div>

    <div class="bot-controls">
      <div class="control-group">
        <label>Base Stake (USD)</label>
        <input type="number" id="base-stake" value="1.00" step="0.5" min="0.35" />
      </div>
      <div class="control-group">
        <label>Martingale Multiplier</label>
        <input type="number" id="martingale" value="2.1" step="0.1" min="1.0" />
      </div>
      <div class="control-group">
        <label>Digit / Prediction</label>
        <input type="number" id="digit-target" value="7" min="0" max="9" />
      </div>
      <button class="btn-start" id="start-btn">Start Bot</button>
    </div>
  </aside>

  <section class="glass-panel execution-terminal">
    <div class="terminal-header">
      <span>Execution Feed</span>
      <span class="terminal-stats">
        W: <span id="stat-wins" style="color:var(--color-neon-green)">0</span>
        &nbsp;L: <span id="stat-losses" style="color:var(--color-neon-red)">0</span>
        &nbsp;P&L: <span id="stat-pnl">$0.00</span>
        &nbsp;Win Rate: <span id="win-rate">0.0%</span>
      </span>
    </div>
    <div class="terminal-logs" id="terminal-logs"></div>
  </section>
`

// ─── Digit Bars ─────────────────────────────────────────────────────────────
const digitContainer = document.getElementById('digit-stats-container')
for (let i = 0; i <= 9; i++) {
  const row = document.createElement('div')
  row.className = 'digit-row'
  row.id = 'digit-row-' + i
  row.innerHTML =
    '<span class="digit-label">' + i + '</span>' +
    '<div class="progress-track">' +
      '<div class="progress-fill" id="fill-' + i + '" style="width:10%"></div>' +
    '</div>' +
    '<span class="digit-value" id="val-' + i + '">--.-%</span>'
  digitContainer.appendChild(row)
}

// ─── Tab Switching ───────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active') })
    tab.classList.add('active')
  })
})

// ─── Chart Tick Count Buttons ────────────────────────────────────────────────
document.querySelectorAll('.tool-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.tool-btn').forEach(function(b) { b.classList.remove('active') })
    btn.classList.add('active')
    chart.setMaxTicks(parseInt(btn.dataset.ticks))
  })
})

// ─── Logging ─────────────────────────────────────────────────────────────────
const logsEl = document.getElementById('terminal-logs')
function addLog(msg, type) {
  const time = new Date().toLocaleTimeString()
  const entry = document.createElement('div')
  entry.className = 'log-entry'
  entry.innerHTML = '<span class="log-time">[' + time + ']</span>' +
                    '<span class="log-msg ' + type + '">' + msg + '</span>'
  logsEl.prepend(entry)
  while (logsEl.children.length > 200) logsEl.removeChild(logsEl.lastChild)
}

// ─── Bot State ───────────────────────────────────────────────────────────────
let isRunning = false
let wins = 0, losses = 0, pnl = 0
let currentStake = 1.0
let martingaleActive = false
let openContractId = null

function updateStats() {
  document.getElementById('stat-wins').textContent = wins
  document.getElementById('stat-losses').textContent = losses
  const pnlEl = document.getElementById('stat-pnl')
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2)
  pnlEl.style.color = pnl >= 0 ? 'var(--color-neon-green)' : 'var(--color-neon-red)'
  const total = wins + losses
  const wr = total > 0 ? ((wins / total) * 100).toFixed(1) : '0.0'
  document.getElementById('win-rate').textContent = wr + '%'
}

// ─── Chart Setup ─────────────────────────────────────────────────────────────
const canvas = document.getElementById('price-chart')
const chart = new TickChart(canvas)

// ─── Analyzer ────────────────────────────────────────────────────────────────
const analyzer = new DigitAnalyzer()

function updateDigitUI() {
  const counts = analyzer.getPercentages()
  const strip = document.getElementById('last-digits-strip')
  const lastDigits = analyzer.getLastDigits(20)

  strip.innerHTML = lastDigits.map(function(d) {
    return '<span class="last-digit">' + d + '</span>'
  }).join('')

  for (let i = 0; i <= 9; i++) {
    const pct = counts[i] || 0
    const fillEl = document.getElementById('fill-' + i)
    const valEl = document.getElementById('val-' + i)
    const rowEl = document.getElementById('digit-row-' + i)

    fillEl.style.width = Math.min(pct * 2, 100) + '%'
    valEl.textContent = pct.toFixed(1) + '%'

    rowEl.classList.remove('is-hot', 'is-cold')
    if (pct >= 12.0) rowEl.classList.add('is-hot')
    else if (pct <= 7.7) rowEl.classList.add('is-cold')
  }
}

// ─── WebSocket ───────────────────────────────────────────────────────────────
const ws = new DerivWS(APP_ID, API_TOKEN)
const connDot = document.getElementById('conn-dot')

ws.onOpen(function() {
  connDot.classList.add('connected')
  connDot.title = 'Connected'
  addLog('WebSocket connected to Deriv API.', 'info')
  ws.authorize()
})

ws.onAuthorized(function(data) {
  const bal = data.authorize.balance.toFixed(2) + ' ' + data.authorize.currency
  document.getElementById('account-balance').textContent = bal
  document.getElementById('account-balance').style.color =
    data.authorize.is_virtual ? 'var(--color-neon-green)' : '#fff'
  addLog('Authorized as: ' + data.authorize.fullname + ' | Balance: ' + bal, 'success')
  subscribeToMarket(document.getElementById('market-select').value)
})

ws.onTick(function(tick) {
  const price = tick.quote
  const priceEl = document.getElementById('live-price')
  priceEl.textContent = price.toFixed(tick.pip_size || 2)

  chart.addPoint(price)
  analyzer.addTick(price)
  updateDigitUI()

  // Bot logic
  if (isRunning && openContractId === null) {
    evaluateEntry()
  }
})

ws.onContractUpdate(function(data) {
  if (!data.proposal_open_contract) return
  const contract = data.proposal_open_contract
  if (contract.is_sold) {
    openContractId = null
    const profit = parseFloat(contract.profit)
    pnl += profit
    const won = profit > 0
    if (won) {
      wins++
      currentStake = parseFloat(document.getElementById('base-stake').value)
      addLog('WIN +$' + Math.abs(profit).toFixed(2) + ' | Stake reset to $' + currentStake.toFixed(2), 'success')
    } else {
      losses++
      const mult = parseFloat(document.getElementById('martingale').value)
      currentStake = parseFloat((currentStake * mult).toFixed(2))
      addLog('LOSS -$' + Math.abs(profit).toFixed(2) + ' | Martingale → next stake $' + currentStake.toFixed(2), 'error')
    }
    updateStats()
  }
})

ws.onError(function(msg) {
  addLog('API Error: ' + msg, 'error')
})

ws.onClose(function() {
  connDot.classList.remove('connected')
  connDot.title = 'Disconnected'
  addLog('Connection lost. Reconnecting...', 'error')
})

ws.connect()

// ─── Market Subscription ─────────────────────────────────────────────────────
function subscribeToMarket(symbol) {
  analyzer.reset()
  chart.reset()
  addLog('Subscribing to ' + symbol + '...', 'info')
  ws.subscribeTicks(symbol)
}

document.getElementById('market-select').addEventListener('change', function() {
  const sym = this.value
  const names = {
    'R_10': 'Volatility 10 Index', 'R_25': 'Volatility 25 Index',
    'R_50': 'Volatility 50 Index', 'R_75': 'Volatility 75 Index',
    'R_100': 'Volatility 100 Index', '1HZ10V': 'Volatility 10 (1s)',
    '1HZ25V': 'Volatility 25 (1s)', '1HZ50V': 'Volatility 50 (1s)',
    '1HZ75V': 'Volatility 75 (1s)', '1HZ100V': 'Volatility 100 (1s)'
  }
  document.getElementById('chart-title').textContent = names[sym] || sym
  subscribeToMarket(sym)
})

// ─── Trade Entry Logic ───────────────────────────────────────────────────────
function evaluateEntry() {
  if (analyzer.tickCount() < 25) return
  const tradeType = document.getElementById('trade-type-select').value
  const digitTarget = parseInt(document.getElementById('digit-target').value)
  const percentages = analyzer.getPercentages()
  let shouldTrade = false

  if (tradeType === 'DIGITMATCH' || tradeType === 'DIGITDIFF') {
    const lastDigit = analyzer.getLastDigit()
    if (lastDigit === 9 && percentages[0] >= 12.0 && percentages[7] <= 7.7) {
      shouldTrade = true
    }
  } else if (tradeType === 'DIGITEVEN' || tradeType === 'DIGITODD') {
    shouldTrade = true // 50/50 flat stake
  } else if (tradeType === 'DIGITOVER' || tradeType === 'DIGITUNDER') {
    if (percentages[digitTarget] >= 12.0) shouldTrade = true
  } else if (tradeType === 'CALL') {
    shouldTrade = true // Rise/Fall uses SMA — future improvement
  }

  if (shouldTrade) {
    placeContract(tradeType, digitTarget)
  }
}

function placeContract(tradeType, digitTarget) {
  const symbol = document.getElementById('market-select').value
  const stake = currentStake
  addLog('Placing ' + tradeType + ' @ $' + stake + ' on ' + symbol, 'info')

  const params = {
    buy: 1,
    price: stake,
    parameters: {
      contract_type: tradeType,
      currency: 'USD',
      basis: 'stake',
      symbol: symbol,
      duration: 2,
      duration_unit: 't'
    }
  }

  if (['DIGITMATCH', 'DIGITDIFF', 'DIGITOVER', 'DIGITUNDER'].includes(tradeType)) {
    params.parameters.barrier = String(digitTarget)
  }

  ws.buy(params, function(res) {
    if (res.error) {
      addLog('Order failed: ' + res.error.message, 'error')
      return
    }
    openContractId = res.buy.contract_id
    addLog('Contract opened: #' + openContractId, 'success')
    ws.subscribeContract(openContractId)
  })
}

// ─── Start/Stop Button ───────────────────────────────────────────────────────
const startBtn = document.getElementById('start-btn')
startBtn.addEventListener('click', function() {
  isRunning = !isRunning
  if (isRunning) {
    currentStake = parseFloat(document.getElementById('base-stake').value)
    startBtn.textContent = 'Stop Bot'
    startBtn.classList.add('running')
    addLog('Bot STARTED. Monitoring entry conditions...', 'success')
  } else {
    startBtn.textContent = 'Start Bot'
    startBtn.classList.remove('running')
    openContractId = null
    addLog('Bot STOPPED by user.', 'error')
  }
})
