// TickChart — draws a live tick price chart on a canvas element
export class TickChart {
  constructor(canvas) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')
    this.prices = []
    this.maxTicks = 60
    this._resize()
    window.addEventListener('resize', () => this._resize())
  }

  _resize() {
    const rect = this.canvas.parentElement.getBoundingClientRect()
    this.canvas.width = rect.width
    this.canvas.height = rect.height
    this.draw()
  }

  setMaxTicks(n) {
    this.maxTicks = n
    this.draw()
  }

  addPoint(price) {
    this.prices.push(price)
    if (this.prices.length > this.maxTicks) {
      this.prices.shift()
    }
    this.draw()
  }

  reset() {
    this.prices = []
    this.draw()
  }

  draw() {
    const ctx = this.ctx
    const w = this.canvas.width
    const h = this.canvas.height
    ctx.clearRect(0, 0, w, h)

    if (this.prices.length < 2) {
      ctx.fillStyle = 'rgba(148,163,184,0.3)'
      ctx.font = '14px Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText('Waiting for live ticks...', w / 2, h / 2)
      return
    }

    const prices = this.prices
    const minP = Math.min(...prices)
    const maxP = Math.max(...prices)
    const range = maxP - minP || 1
    const pad = { top: 20, bottom: 30, left: 60, right: 20 }
    const chartW = w - pad.left - pad.right
    const chartH = h - pad.top - pad.bottom

    const toX = (i) => pad.left + (i / (prices.length - 1)) * chartW
    const toY = (p) => pad.top + chartH - ((p - minP) / range) * chartH

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.05)'
    ctx.lineWidth = 1
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (i / 4) * chartH
      ctx.beginPath()
      ctx.moveTo(pad.left, y)
      ctx.lineTo(pad.left + chartW, y)
      ctx.stroke()

      const price = (maxP - (i / 4) * range).toFixed(2)
      ctx.fillStyle = 'rgba(148,163,184,0.6)'
      ctx.font = '11px Inter, monospace'
      ctx.textAlign = 'right'
      ctx.fillText(price, pad.left - 6, y + 4)
    }

    // Gradient fill under line
    const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH)
    gradient.addColorStop(0, 'rgba(59,130,246,0.25)')
    gradient.addColorStop(1, 'rgba(59,130,246,0.0)')

    ctx.beginPath()
    prices.forEach((p, i) => {
      const x = toX(i), y = toY(p)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.lineTo(toX(prices.length - 1), pad.top + chartH)
    ctx.lineTo(toX(0), pad.top + chartH)
    ctx.closePath()
    ctx.fillStyle = gradient
    ctx.fill()

    // Price line
    ctx.beginPath()
    prices.forEach((p, i) => {
      const x = toX(i), y = toY(p)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.strokeStyle = '#3B82F6'
    ctx.lineWidth = 2
    ctx.lineJoin = 'round'
    ctx.stroke()

    // Latest price dot + label
    const lastX = toX(prices.length - 1)
    const lastY = toY(prices[prices.length - 1])
    ctx.beginPath()
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2)
    ctx.fillStyle = '#10B981'
    ctx.fill()
    ctx.strokeStyle = '#fff'
    ctx.lineWidth = 1.5
    ctx.stroke()
  }
}
