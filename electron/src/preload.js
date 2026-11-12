const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  minimize: () => ipcRenderer.send('window-minimize'),
  close: () => ipcRenderer.send('window-close'),
  toggleHud: () => ipcRenderer.send('toggle-hud'),
  setAlwaysOnTop: (val) => ipcRenderer.send('toggle-always-on-top', val),

  onMarketData: (cb) => {
    ipcRenderer.removeAllListeners('market-data')
    ipcRenderer.on('market-data', (_, data) => cb(data))
  },
  onWsStatus: (cb) => {
    ipcRenderer.removeAllListeners('ws-status')
    ipcRenderer.on('ws-status', (_, status) => cb(status))
  },
  onHudMode: (cb) => {
    ipcRenderer.removeAllListeners('hud-mode')
    ipcRenderer.on('hud-mode', (_, isHud) => cb(isHud))
  },

  // Renderer → Python (via main process)
  sendCommand: (cmd) => ipcRenderer.send('send-command', cmd),

  // Python → Renderer: config state updates
  onConfig: (cb) => {
    ipcRenderer.removeAllListeners('config-update')
    ipcRenderer.on('config-update', (_, data) => cb(data))
  },

  // Python → Renderer: candle/indicator data for modal
  onCandles: (cb) => {
    ipcRenderer.removeAllListeners('candle-data')
    ipcRenderer.on('candle-data', (_, data) => cb(data))
  },
})