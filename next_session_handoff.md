# Toss Trading — 다음 대화 인수인계 문서

## 현재 상태

Electron 데스크탑 앱 + Python 백엔드 구조.
이번 세션에서 4가지 기능을 모두 구현 완료.

---

## 이번 세션에서 완료한 작업

### 1. 자동매매 ON/OFF 토글
- 타이틀바에 토글 스위치 추가
- `TradingSystem.auto_trade` 플래그로 자동 주문 분기 제어
- `_on_candles`에서 `self.auto_trade` 체크 후 executor 호출

### 2. DRY RUN / 실전 모드 전환
- 타이틀바의 `DRY RUN` 뱃지 클릭으로 토글
- 실전 전환 시 뱃지가 빨간색 "실전"으로 변경
- 런타임에 `executor.dry_run` 값을 변경 (재시작 불필요)

### 3. 종목 카드 수동 매수/매도 버튼
- 각 카드 오른쪽에 B(매수) / S(매도) 버튼 추가
- 카드 클릭 → 모달, 버튼 클릭 → 즉시 수동 주문 (이벤트 버블링 차단)
- `manual_order` 커맨드 → `executor.execute(JudgmentResult(...))` 실행

### 4. 종목 클릭 시 상세 모달
- Canvas 기반 캔들차트 (40개 봉, 색상 BUY=#00D4AA / SELL=#FF6B6B)
- RSI / MACD / 볼린저밴드 지표 카드
- Python이 아직 응답 안 했을 때 mock 캔들로 즉시 렌더링
- Python 캔들 수신 시 실제 데이터로 교체

---

## 프로젝트 구조

```
toss-trading/
├── main.py                  # 진입점 — asyncio로 전체 실행
├── config.yaml              # 설정 (API 키, dry_run, tickers 등)
├── config_loader.py         # config.yaml 로더
├── ui_server.py             # Python ↔ Electron WebSocket 브릿지 (ws://localhost:8765)
├── collector/
│   ├── toss_api.py          # 토스증권 API 인터페이스 (현재 Mock)
│   └── websocket_client.py  # 실시간 데이터 수집 + 캔들 버퍼
├── analyzer/
│   └── technical.py         # RSI, MACD, 볼린저밴드 등 지표 계산
├── ai/
│   ├── judgment_engine.py   # Gemini 2.5 Flash API 판단 엔진 (무료 티어)
│   └── prompt_templates.py  # AI 프롬프트
├── executor/
│   ├── order.py             # 주문 실행 + SQLite 기록
│   └── risk_manager.py      # 리스크 체크
└── electron/
    ├── package.json
    └── src/
        ├── main.js          # Electron 메인 프로세스
        ├── preload.js       # IPC 보안 브릿지
        └── index.html       # 대시보드 UI
```

---

## 기술 스택

- **Python 3.14** (Windows)
- **Electron 28** + HTML/CSS/JS (단일 파일 index.html)
- **WebSocket** — Python(ui_server.py) ↔ Electron(main.js) 통신
- **IPC** — main.js ↔ index.html (preload.js 통해서)
- **Gemini 2.5 Flash** — AI 매매 판단 (google-genai 패키지)
- **pandas, numpy** — 기술적 지표 계산
- **SQLite** — 주문 내역 저장

---

## WebSocket 메시지 형식

### Python → Electron

```json
{ "type": "tick",    "ticker": "005930", "price": 75400, "volume": 500000 }
{ "type": "signal",  "ticker": "005930", "signal": "BUY", "confidence": 84, "reason": "RSI 과매도", "target_price": 76500 }
{ "type": "order",   "ticker": "005930", "side": "BUY", "qty": 13, "price": 75400, "pnl": 0 }
{ "type": "balance", "total_asset": 10000000, "today_pnl": 0 }
{ "type": "config",  "auto_trade": true, "dry_run": true }
{ "type": "candles", "ticker": "005930", "ohlcv": [...], "indicators": {...} }
```

`ohlcv` 항목 형식: `{"o": int, "h": int, "l": int, "c": int, "v": int}`

`indicators` 키: `rsi`, `macd`, `macd_signal`, `bb_upper`, `bb_lower`, `bb_mid`
(analyze() 반환값 그대로 전달)

### Electron → Python

```json
{ "type": "set_auto_trade", "value": true }
{ "type": "set_dry_run",    "value": false }
{ "type": "manual_order",   "ticker": "005930", "side": "BUY", "price": 75400 }
{ "type": "get_candles",    "ticker": "005930" }
{ "type": "get_config" }
```

---

## IPC 채널 (preload.js)

```javascript
window.api.minimize()                  // 창 최소화
window.api.close()                     // 트레이로 숨기기
window.api.toggleHud()                 // HUD 미니 모드
window.api.sendCommand(cmd)            // Electron → Python 커맨드 전송
window.api.onMarketData(callback)      // tick/signal/order/balance 수신
window.api.onWsStatus(callback)        // 'connected' | 'disconnected'
window.api.onHudMode(callback)         // HUD 모드 여부
window.api.onConfig(callback)          // config 상태 수신 (auto_trade, dry_run)
window.api.onCandles(callback)         // 캔들 + 지표 데이터 수신
```

---

## ui_server.py 주요 메서드

```python
ui.on_command(async_callback)          # Electron → Python 커맨드 핸들러 등록
ui.on_connect(async_callback)          # Electron 연결 시 콜백 (config 초기 전송용)
await ui.send_tick(ticker, price, vol)
await ui.send_signal(ticker, signal, confidence, reason, target_price)
await ui.send_order(ticker, side, qty, price, pnl)
await ui.send_balance(total_asset, today_pnl)
await ui.send_config(auto_trade, dry_run)
await ui.send_candles(ticker, ohlcv, indicators)
```

---

## main.py TradingSystem 주요 속성/메서드

```python
self.auto_trade: bool       # 자동매매 ON/OFF (기본 True)
self.dry_run: bool          # DRY RUN 여부
self.collector: DataCollector
self.executor: OrderExecutor
self.ui: UIServer

async _on_connect()         # Electron 연결 시 → send_config 전송
async _on_command(cmd)      # 커맨드 라우터 (set_auto_trade / set_dry_run / manual_order / get_candles)
async _on_candles(ticker, df)  # 캔들 수신 → 분석 → AI → 실행 파이프라인
```

---

## 디자인 시스템

| 변수 | 값 | 용도 |
|------|----|------|
| `--bg` | `#0A1628` | 배경 |
| `--surface` | `#0D1E38` | 카드/패널 |
| `--surface2` | `#112040` | 내부 중첩 |
| `--mint` | `#00D4AA` | 매수/상승/ON |
| `--red` | `#FF6B6B` | 매도/하락/실전 |
| `--amber` | `#FFB347` | HOLD/DRY RUN 경고 |

---

## 남은 작업 (다음 세션 후보)

### 우선순위 높음
- **텔레그램 알림 / Human-in-the-loop** — `_on_candles`에 `TODO` 주석 있음
  - 신뢰도 60~80% 구간에서 텔레그램 메시지 발송 → 사용자 응답으로 주문 실행
- **실전 전환 시 확인 다이얼로그** — 현재 UI 토글 클릭 즉시 전환됨, "정말 실전으로 전환할까요?" 모달 추가 권장
- **잔고/포지션 실시간 반영** — `send_balance` 호출 주기 설정 필요 (현재 연결 시 1회만 전송)

### 우선순위 중간
- **종목 추가/삭제 UI** — 현재 config.yaml 수정 후 재시작 필요
- **모달 캔들차트 개선** — 현재 1분봉 40개. 시간 범위 선택(5분/1시간) 추가
- **주문 내역 상세** — 현재 최근 10개만 표시. 페이지네이션 또는 전체 보기 추가
- **RSI 미니차트** — 지표 카드에 숫자만 표시 중. 선차트 추가

### 우선순위 낮음
- **테마/설정 패널** — 종목 비중, 신뢰도 임계값 UI에서 조정
- **수익 그래프** — 오늘 수익 추이 차트
- **백테스트 결과 화면** — 별도 탭 또는 모달

---

## 주의사항

- `index.html` 단일 파일에 CSS + JS 모두 포함 (분리 금지)
- Windows 환경 — `transparent: false` 유지
- Python 로그는 영어로 (한글 로그 깨짐 이슈)
- `analyze()` 반환값 키가 pandas-ta 버전마다 다를 수 있음 → JS에서 `parseFloat`로 안전하게 처리 중
- `get_candles` 핸들러는 `collector._buffers`에 직접 접근 — 추후 public API로 노출 권장
