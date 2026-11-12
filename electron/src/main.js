const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, Notification, screen } = require('electron')
const path = require('path')
const { WebSocket } = require('ws')

let mainWindow = null
let tray = null
let isHudMode = false
let wsClient = null

// ── 창 생성 ──────────────────────────────────────────────────────────────────

function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize

  mainWindow = new BrowserWindow({
    width: 620,
    height: 800,
    minWidth: 480,
    minHeight: 600,
    x: width - 640,
    y: 30,
    frame: false,
    transparent: false,       // Windows에서 transparent 오류 방지
    backgroundColor: '#0A1628',
    resizable: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  mainWindow.loadFile(path.join(__dirname, 'index.html'))

  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  }

  mainWindow.on('closed', () => { mainWindow = null })
}

// ── 트레이 아이콘 ─────────────────────────────────────────────────────────────

function createTray() {
  // Windows에서 빈 아이콘 대신 1x1 픽셀 이미지 사용
  const icon = nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
  )
  tray = new Tray(icon)
  tray.setToolTip('Toss Trading')

  const contextMenu = Menu.buildFromTemplate([
    { label: '대시보드 열기', click: () => { mainWindow?.show(); mainWindow?.focus() } },
    { label: 'HUD 미니 모드', type: 'checkbox', checked: isHudMode, click: () => toggleHudMode() },
    { type: 'separator' },
    { label: '항상 위에 표시', type: 'checkbox', checked: true, click: (item) => mainWindow?.setAlwaysOnTop(item.checked) },
    { type: 'separator' },
    { label: '종료', click: () => app.quit() },
  ])

  tray.setContextMenu(contextMenu)
  tray.on('click', () => {
    if (mainWindow?.isVisible()) { mainWindow.hide() }
    else { mainWindow?.show(); mainWindow?.focus() }
  })
}

// ── HUD 미니 모드 (작은 플로팅 위젯) ──────────────────────────────────────────

function toggleHudMode() {
  isHudMode = !isHudMode
  if (!mainWindow) return

  if (isHudMode) {
    mainWindow.setSize(380, 220)
    mainWindow.setResizable(false)
  } else {
    mainWindow.setSize(620, 800)
    mainWindow.setResizable(true)
  }
  mainWindow.webContents.send('hud-mode', isHudMode)
}

// ── Python WebSocket 연결 ─────────────────────────────────────────────────────

function connectToPython() {
  const WS_URL = 'ws://localhost:8765'

  function connect() {
    wsClient = new WebSocket(WS_URL)

    wsClient.on('open', () => {
      console.log('[WS] Connected to Python server')
      mainWindow?.webContents.send('ws-status', 'connected')
    })

    wsClient.on('message', (data) => {
      try {
        const msg = JSON.parse(data.toString())

        if (msg.type === 'config') {
          mainWindow?.webContents.send('config-update', msg)
        } else if (msg.type === 'candles') {
          mainWindow?.webContents.send('candle-data', msg)
        } else {
          mainWindow?.webContents.send('market-data', msg)
        }

        if (msg.type === 'signal' && msg.signal === 'BUY' && msg.confidence >= 80) {
          showNotification(msg)
        }
      } catch (e) {
        console.error('[WS] parse error:', e)
      }
    })

    wsClient.on('close', () => {
      console.log('[WS] Disconnected — reconnecting in 3s')
      mainWindow?.webContents.send('ws-status', 'disconnected')
      setTimeout(connect, 3000)
    })

    wsClient.on('error', () => {
      // 서버 아직 안 뜬 경우 조용히 재시도
      wsClient.terminate()
    })
  }

  connect()
}

// ── OS 알림 ──────────────────────────────────────────────────────────────────

function showNotification(msg) {
  if (!Notification.isSupported()) return
  new Notification({
    title: `📈 매수 신호 — ${msg.ticker}`,
    body: `신뢰도 ${msg.confidence}%  목표가 ${msg.target_price?.toLocaleString()}원\n${msg.reason}`,
    urgency: 'normal',
  }).show()
}

// ── IPC (renderer → main) ────────────────────────────────────────────────────

ipcMain.on('window-minimize', () => mainWindow?.minimize())
ipcMain.on('window-close', () => mainWindow?.hide())
ipcMain.on('toggle-hud', () => toggleHudMode())
ipcMain.on('toggle-always-on-top', (_, val) => mainWindow?.setAlwaysOnTop(val))
ipcMain.on('send-command', (_, cmd) => {
  if (wsClient && wsClient.readyState === WebSocket.OPEN) {
    wsClient.send(JSON.stringify(cmd))
  }
})

// ── 앱 생명주기 ───────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createWindow()
  createTray()
  connectToPython()

  app.on('activate', () => {
    if (!mainWindow) createWindow()
  })
})

app.on('window-all-closed', () => {
  // macOS: 창 닫아도 앱 유지 (트레이로)
  if (process.platform !== 'darwin') app.quit()
})