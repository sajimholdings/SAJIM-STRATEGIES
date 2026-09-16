// DigitAnalyzer — tracks digit occurrence probabilities from live ticks
export class DigitAnalyzer {
  constructor() {
    this.ticks = []
    this.counts = new Array(10).fill(0)
    this.maxHistory = 1000
  }

  addTick(price) {
    // Extract last digit from the price (after decimal point)
    const str = price.toString()
    const lastChar = str[str.length - 1]
    const digit = parseInt(lastChar)

    if (isNaN(digit)) return

    this.ticks.push(digit)
    this.counts[digit]++

    // Rolling window: drop oldest tick if over maxHistory
    if (this.ticks.length > this.maxHistory) {
      const removed = this.ticks.shift()
      this.counts[removed]--
    }
  }

  getPercentages() {
    const total = this.ticks.length
    if (total === 0) return new Array(10).fill(0)
    return this.counts.map((c) => (c / total) * 100)
  }

  getLastDigit() {
    if (this.ticks.length === 0) return null
    return this.ticks[this.ticks.length - 1]
  }

  getLastDigits(n) {
    return this.ticks.slice(-n).reverse()
  }

  tickCount() {
    return this.ticks.length
  }

  reset() {
    this.ticks = []
    this.counts = new Array(10).fill(0)
  }
}
