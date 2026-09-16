// DerivWS — WebSocket wrapper for Deriv API
export class DerivWS {
  constructor(appId, token) {
    this.appId = appId
    this.token = token
    this.ws = null
    this.tickForget = null // forget_id for active tick subscription

    this._onOpenCb = null
    this._onAuthCb = null
    this._onTickCb = null
    this._onContractCb = null
    this._onErrorCb = null
    this._onCloseCb = null

    this._buyCallbacks = {}
    this._reqId = 1
  }

  connect() {
    const url = 'wss://ws.derivws.com/websockets/v3?app_id=' + this.appId
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      if (this._onOpenCb) this._onOpenCb()
    }

    this.ws.onclose = () => {
      if (this._onCloseCb) this._onCloseCb()
      // Auto-reconnect after 3s
      setTimeout(() => this.connect(), 3000)
    }

    this.ws.onerror = (e) => {
      if (this._onErrorCb) this._onErrorCb('WebSocket error')
    }

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      this._dispatch(data)
    }

    // Keepalive ping every 30s
    this._pingInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this._send({ ping: 1 })
      }
    }, 30000)
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj))
    }
  }

  _dispatch(data) {
    if (data.error) {
      if (this._onErrorCb) this._onErrorCb(data.error.message)
      return
    }

    switch (data.msg_type) {
      case 'authorize':
        if (this._onAuthCb) this._onAuthCb(data)
        break
      case 'tick':
        // Store the subscription id so we can forget it later
        if (data.subscription) this.tickForget = data.subscription.id
        if (this._onTickCb) this._onTickCb(data.tick)
        break
      case 'proposal_open_contract':
        if (this._onContractCb) this._onContractCb(data)
        break
      case 'buy': {
        const cb = this._buyCallbacks[data.req_id]
        if (cb) { cb(data); delete this._buyCallbacks[data.req_id] }
        break
      }
      case 'ping':
        // pong received — connection alive
        break
    }
  }

  authorize() {
    this._send({ authorize: this.token })
  }

  subscribeTicks(symbol) {
    // Forget old subscription first
    if (this.tickForget) {
      this._send({ forget: this.tickForget })
      this.tickForget = null
    }
    this._send({ ticks: symbol, subscribe: 1 })
  }

  subscribeContract(contractId) {
    this._send({
      proposal_open_contract: 1,
      contract_id: contractId,
      subscribe: 1
    })
  }

  buy(params, callback) {
    const id = this._reqId++
    this._buyCallbacks[id] = callback
    const msg = Object.assign({}, params, { req_id: id })
    this._send(msg)
  }

  // Event hooks
  onOpen(cb) { this._onOpenCb = cb }
  onAuthorized(cb) { this._onAuthCb = cb }
  onTick(cb) { this._onTickCb = cb }
  onContractUpdate(cb) { this._onContractCb = cb }
  onError(cb) { this._onErrorCb = cb }
  onClose(cb) { this._onCloseCb = cb }
}
