# 자동매매 시스템 사용 설명서

> 최종 업데이트: 2026-05-20  
> Python 3.11+, Electron 28 기반

---

## 목차

1. [최초 설치](#1-최초-설치)
2. [config.yaml 설정](#2-configyaml-설정)
3. [시스템 실행](#3-시스템-실행)
4. [UI 사용법](#4-ui-사용법)
5. [자동매매 파이프라인](#5-자동매매-파이프라인)
6. [리스크 관리](#6-리스크-관리)
7. [트레일링 스탑 & 부분 익절](#7-트레일링-스탑--부분-익절)
8. [멀티 타임프레임 분석](#8-멀티-타임프레임-분석)
9. [성과 분석](#9-성과-분석)
10. [백테스트](#10-백테스트)
11. [UI 종목 관리](#11-ui-종목-관리)
12. [설정 파라미터 변경 (런타임)](#12-설정-파라미터-변경-런타임)
13. [WebSocket 커맨드 레퍼런스](#13-websocket-커맨드-레퍼런스)
14. [DB 구조](#14-db-구조)
15. [테스트 실행](#15-테스트-실행)
16. [실전 전환 체크리스트](#16-실전-전환-체크리스트)
17. [자주 묻는 질문](#17-자주-묻는-질문)

---

## 1. 최초 설치

### Python 패키지

```bash
pip install pandas numpy pandas-ta google-genai websockets pyyaml pytest
```

### Electron (UI)

```bash
cd electron
npm install
npm start
```

### 디렉토리 구조

```
toss-trading/
├── main.py                   # 백엔드 진입점
├── config.yaml               # 설정 파일 (Git 제외)
├── config_loader.py          # 설정 로더 (런타임 변경 지원)
├── ui_server.py              # WebSocket 서버 (Python ↔ Electron)
├── collector/
│   ├── toss_api.py           # 토스증권 API (현재 Mock)
│   └── websocket_client.py   # 실시간 데이터 수집기
├── analyzer/
│   └── technical.py          # 기술적 지표 (분봉 + 일봉)
├── ai/
│   ├── judgment_engine.py    # Gemini 판단 엔진
│   └── prompt_templates.py   # AI 프롬프트 관리
├── executor/
│   ├── order.py              # 주문 실행 + DB 저장 + 성과 집계
│   └── risk_manager.py       # 리스크 체크
├── backtest/
│   └── engine.py             # 룰 기반 백테스트 엔진
├── data/trading.db           # SQLite (자동 생성)
├── logs/trading.log          # 로그 파일 (자동 생성)
└── tests/test_all.py         # 테스트 스위트
```

---

## 2. config.yaml 설정

> `config.yaml`은 `.gitignore`에 포함됩니다. 키를 커밋하지 마세요.

### 필수 설정

```yaml
# Gemini AI API 키 (https://aistudio.google.com/apikey 에서 무료 발급)
gemini:
  api_key: "AIza..."
  model: "gemini-3.1-flash-lite"
  daily_limit: 500    # 앱 내부 일일 호출 한도
  rpm_limit: 15       # 앱 내부 분당 호출 한도

# 토스증권 API (실제 연동 구현 전까지 mock_mode: true 유지)
toss:
  app_key: "YOUR_APP_KEY_HERE"
  app_secret: "YOUR_APP_SECRET_HERE"
  mock_mode: true

# 매매 설정
trading:
  dry_run: true           # 반드시 true로 시작!
  tickers:                # KR 종목
    - "005930"            # 삼성전자
    - "000660"            # SK하이닉스
  us_tickers:             # US 종목
    - "AAPL"
    - "TSLA"
    - "NVDA"
  interval_seconds: 300   # AI 판단 주기 (5분 권장)
```

### 전체 설정 항목

```yaml
trading:
  dry_run: true

  # 종목
  tickers: ["005930", "000660", "035420"]   # KR
  us_tickers: ["AAPL", "TSLA", "NVDA"]     # US

  # 장 시간 (KR)
  market_open: "09:00"
  market_close: "15:30"
  # US는 자동 감지 (KST 23:30 ~ 06:00)

  # 리스크
  risk:
    max_position_ratio: 0.20       # 단일 주문 최대 총자산 대비 20%
    max_total_position_ratio: 0.40 # 종목 총 보유 한도 40% (기존+신규 합산)
    daily_loss_limit: 0.03         # 일일 손실 3% 초과 시 매매 중단
    consecutive_stop_limit: 3      # 연속 손절 3회 시 쿨다운
    cooldown_minutes: 60           # 쿨다운 시간 (분)

  # AI 임계값
  confidence:
    auto_execute: 80    # 신뢰도 80% 이상 → 자동 주문
    ask_human: 60       # 신뢰도 60~79% → 로그만 남김 (텔레그램 미구현)

  # 트레일링 스탑
  trailing_stop_pct: 0.03    # 고점 대비 3% 하락 시 자동 매도

  # 부분 익절
  partial_take_profit: 0.5   # 목표가 도달 시 50% 매도 (0이면 전량)

  # 일봉 데이터 갱신 주기
  daily_refresh_minutes: 30

  # 수수료
  commission:
    kr_buy_rate: 0.00015    # KR 매수 0.015%
    kr_sell_rate: 0.00195   # KR 매도 0.195% (증권거래세 포함)
    us_buy_rate: 0.0025     # US 매수/매도 0.25%
    us_sell_rate: 0.0025

database:
  path: "data/trading.db"

logging:
  level: "INFO"             # DEBUG | INFO | WARNING | ERROR
  file: "logs/trading.log"
  max_bytes: 10485760       # 10MB
  backup_count: 5
```

---

## 3. 시스템 실행

### 백엔드 시작

```bash
# DRY-RUN 모드 (기본, 실제 주문 없음)
python main.py

# 실전 모드 (주의!)
python main.py --live

# 설정 파일 경로 지정
python main.py --config /path/to/config.yaml
```

### Electron UI 시작

```bash
cd electron
npm start
```

> 백엔드(`python main.py`)를 먼저 시작하고 Electron을 열어야 합니다.  
> UI는 `ws://localhost:8765`로 자동 연결됩니다.

### 실행 순서

```
1. python main.py 실행
2. "UI 서버 시작 ws://localhost:8765" 로그 확인
3. npm start (Electron)
4. UI 상단에 "● 연결됨" 표시 확인
5. 잔고·포지션 표시 확인
```

---

## 4. UI 사용법

### 상단 헤더

| 요소 | 설명 |
|------|------|
| `KR / US` 토글 | 한국/미국 시장 전환 (확인 모달 뜸) |
| `자동매매` 토글 | ON 시 신뢰도 80%+ 자동 주문 실행 |
| `AI` 토글 | OFF 시 AI 판단 중단, 자동매매도 함께 OFF |
| `DRY RUN` 토글 | OFF 시 실전 전환 (확인 모달 뜸) |

### 종목 카드

각 종목 카드에서:
- **현재가**: 틱 수신마다 실시간 갱신
- **AI 신호**: BUY(초록) / SELL(빨강) / HOLD(회색) + 신뢰도 %
- **목표가 / 손절가**: AI가 설정한 기준선
- **AI 의견 보기**: 클릭 시 판단 근거 텍스트 표시

### 수동 주문 모달

1. 종목 카드의 **주문** 버튼 클릭
2. BUY / SELL 선택
3. 가격·수량 입력 (KR: 원 단위, US: 달러)
4. **주문 실행** 클릭
5. 우측 하단 토스트로 결과 확인

### HUD 미니 모드

우측 상단 `□` 버튼 → 작은 플로팅 창으로 전환  
실시간 가격만 보고 싶을 때 사용

---

## 5. 자동매매 파이프라인

```
[틱 수신] (5초 간격 Mock, 실전 시 실시간)
    │
    ├─→ UI 실시간 가격 전송
    │
    └─→ 손절가/목표가 도달 체크
            │
            ├─ 손절가 도달 → execute_stop_loss() [쿨다운 면제]
            ├─ 목표가 도달 → execute_take_profit() [부분/전량]
            └─ 트레일링 스탑 발동 → execute_stop_loss()

[5분마다 캔들 수신]
    │
    ├─ 분봉 기술적 지표 계산 (RSI, MACD, 볼린저밴드, MA)
    ├─ 일봉 지표 조회 (30분 캐시, MA60, RSI, 52주 고저)
    ├─ 포지션 컨텍스트 조회 (수량, 평균단가, 평가손익%)
    ├─ 직전 AI 신호 로드 (DB 영속, 재시작 후에도 유지)
    │
    └─→ Gemini 판단 (기본 한도: 500회/일, 15회/분)
            │
            ├─ 신뢰도 80%+ AND 자동매매 ON → 주문 실행
            ├─ 신뢰도 60~79% → 로그만 (향후 텔레그램 연동)
            └─ 신뢰도 60% 미만 → 무시
```

### AI가 받는 정보

```
종목코드: 005930 (KRW)
현재가: 75,000원

[현재 보유 포지션]
- 보유 수량: 10주
- 평균 매입가: 74,500원
- 현재 평가손익: +0.67% (수익 중)

[직전 AI 판단]
- BUY (신뢰도 85%)

[일봉 지표 (중장기 추세)]
- 일봉 RSI(14): 58.3
- 일봉 이동평균 배열: bullish (MA60 대비 +2.15%)
- 52주 고점: 85,000원 / 저점: 60,000원 (현재 위치: 60.0%)

[분봉 기술적 지표 (단기)]
- RSI(14): 52.3
- MACD: 45.23 / 골든크로스 발생
- 볼린저밴드 위치: 0.62 (0=하단, 1=상단)
- 이동평균 배열: bullish (MA20 대비 +1.25%)
- 거래량 비율: 1.35배 (20일 평균 대비)
- 지지선: 72,000원
- 저항선: 78,000원
```

---

## 6. 리스크 관리

### 매수 시 체크 (순서대로)

1. **쿨다운 중?** → 거부 (단, 손절가 도달 시 면제)
2. **일일 손실 3% 초과?** → 거부
3. **단일 주문 비중 > 20%?** → 거부
4. **종목 총 보유(기존+신규) > 40%?** → 거부

### 매도 시 체크

- **일반 매도**: 쿨다운 + 일일 손실 한도 체크
- **손절가 도달 자동 매도**: 체크 없이 강제 실행 (`force=True`)
- **트레일링 스탑**: 강제 실행

### 연속 손절 쿨다운

```
손절 3회 누적 → 60분간 신규 매수/매도 차단
(손절 강제 매도만 허용)
수익 발생 시 → 연속 손절 카운트 0으로 초기화
매일 자정 → 일일 손실 + 연속 손절 카운트 초기화
```

---

## 7. 트레일링 스탑 & 부분 익절

### 트레일링 스탑

고점을 추적하다가 일정 % 하락 시 자동 매도합니다.

```yaml
trading:
  trailing_stop_pct: 0.03  # 고점 대비 3% 하락 시 발동
```

**동작 예시 (삼성전자)**
```
매수가: 75,000원
→ 가격 상승: 78,000원 (고점 갱신)
→ 가격 하락: 75,660원 = 78,000 × (1 - 0.03)
→ 트레일링 스탑 발동 → 자동 매도
```

- BUY 신호 시 고점 초기화
- 신고점 경신 시마다 기준선 상향 조정
- 재시작 시 목표가 기준으로 고점 복원 (보수적)

### 부분 익절

```yaml
trading:
  partial_take_profit: 0.5  # 목표가 도달 시 50% 매도
                             # 0 또는 미설정 시 전량 매도
```

**동작 예시**
```
보유: 10주, 목표가: 80,000원
→ 80,000원 도달
→ 5주 (50%) 즉시 매도
→ 2차 목표가 자동 설정: 80,000 × 1.05 = 84,000원
→ 나머지 5주는 84,000원에서 익절 또는 트레일링 스탑으로 관리
```

---

## 8. 멀티 타임프레임 분석

AI가 단기(분봉)와 중장기(일봉) 지표를 함께 받습니다.

### 일봉 지표 (30분마다 갱신)

| 지표 | 설명 |
|------|------|
| `daily_ma20` | 일봉 20일 이동평균 |
| `daily_ma60` | 일봉 60일 이동평균 |
| `daily_ma120` | 일봉 120일 이동평균 |
| `daily_ma_arrangement` | `bullish` / `bearish` / `neutral` |
| `daily_rsi` | 일봉 RSI(14) |
| `week52_high` | 52주 최고가 |
| `week52_low` | 52주 최저가 |
| `week52_position` | 현재가가 52주 범위 내 몇 % 위치 (0%=저점, 100%=고점) |

갱신 주기 조정:
```yaml
trading:
  daily_refresh_minutes: 30  # 기본값, 더 자주 갱신하려면 줄이기
```

---

## 9. 성과 분석

잔고 조회 시마다 자동으로 UI에 전송됩니다.

### 집계 항목

| 지표 | 설명 |
|------|------|
| `total_trades` | 총 거래 횟수 |
| `win_rate` | 승률 (0~1) |
| `total_pnl` | 총 순손익 (수수료 포함) |
| `avg_profit` | 평균 수익 (승리 거래) |
| `avg_loss` | 평균 손실 (패배 거래) |
| `profit_factor` | 총 수익 / 총 손실 (1 이상이면 수익) |
| `mdd` | 최대 낙폭 (Maximum Drawdown) |
| `sharpe` | 샤프 지수 근사 (연환산, 무위험수익률=0) |
| `daily_pnl` | 날짜별 손익 리스트 |

### 코드에서 직접 조회

```python
from executor.order import OrderExecutor
executor = OrderExecutor()

# 최근 30일 성과
perf = executor.get_performance(days=30)
print(f"승률: {perf['win_rate']:.1%}")
print(f"MDD: {perf['mdd']:,.0f}원")
print(f"샤프: {perf['sharpe']:.2f}")
```

---

## 10. 백테스트

### 코드에서 사용

```python
from backtest.engine import BacktestEngine
from collector.toss_api import generate_ohlcv

# Mock 데이터로 백테스트 (500 캔들)
df = generate_ohlcv("005930", count=500, base_price=75000)
engine = BacktestEngine(
    initial_cash=10_000_000,   # 시작 자금
    position_ratio=0.95,       # 가용 현금의 95% 투입
    stop_loss_pct=0.05,        # 5% 손절
    take_profit_pct=0.10,      # 10% 익절
    commission_pct=0.00195,    # 매도 수수료 (KR)
)
result = engine.run(df, ticker="005930")
print(result)
# → [005930] 수익률=+12.34% 거래=8회 승률=62.5% MDD=4.56% 샤프=1.23
```

### 결과 상세 조회

```python
# 개별 거래 내역
for trade in result.trades:
    print(f"{trade.entry_date} → {trade.exit_date} "
          f"진입 {trade.entry_price:,} 청산 {trade.exit_price:,} "
          f"손익 {trade.pnl:+,.0f}원 ({trade.pnl_pct:+.2f}%) "
          f"사유: {trade.exit_reason}")

# 자산 곡선
for point in result.equity_curve[-5:]:  # 마지막 5일
    print(f"{point['date']}: {point['equity']:,.0f}원")
```

### US 주식 백테스트

```python
df = generate_ohlcv("AAPL", count=500, base_price=191.0)
engine = BacktestEngine(
    initial_cash=10_000,
    commission_pct=0.0025,  # US 수수료
)
result = engine.run(df, ticker="AAPL")
```

### 전략 (내장 룰)

| 신호 | 조건 |
|------|------|
| **BUY** | RSI < 35 **AND** MACD 골든크로스 **AND** 볼린저밴드 하단(< 0.3) |
| **SELL** | RSI > 65 **OR** MACD 데드크로스 **OR** 볼린저밴드 상단(> 0.7) |
| **손절** | 진입가 대비 -5% |
| **익절** | 진입가 대비 +10% |

---

## 11. UI 종목 관리

UI에서 종목을 추가/삭제하면 `config.yaml`에 자동 저장됩니다.

### WebSocket 커맨드로 직접 제어

```javascript
// Electron 렌더러에서
window.electronAPI.sendCommand({
  type: "add_ticker",
  ticker: "035720"   // 카카오 추가
})

window.electronAPI.sendCommand({
  type: "remove_ticker",
  ticker: "035420"   // NAVER 제거
})
```

### 규칙

- 최소 1개 종목은 유지
- 종목 추가/삭제 후 콜렉터 자동 재시작
- KR/US 시장 전환 시 각 시장 목록 독립 관리
- `config.yaml`에 즉시 저장 (재시작 후에도 유지)

---

## 12. 설정 파라미터 변경 (런타임)

앱 재시작 없이 설정값을 바꿀 수 있습니다.

### 허용된 변경 가능 키

```
trading.risk.max_position_ratio       단일 주문 비중 한도
trading.risk.daily_loss_limit         일일 손실 한도
trading.risk.consecutive_stop_limit   연속 손절 횟수
trading.risk.cooldown_minutes         쿨다운 시간(분)
trading.trailing_stop_pct             트레일링 스탑 %
trading.partial_take_profit           부분 익절 비율
trading.daily_refresh_minutes         일봉 갱신 주기
trading.confidence.auto_execute       자동 실행 최소 신뢰도
trading.confidence.ask_human          확인 요청 최소 신뢰도
gemini.daily_limit                    Gemini 일일 한도
gemini.rpm_limit                      Gemini 분당 한도
```

### WebSocket 커맨드로 변경

```javascript
window.electronAPI.sendCommand({
  type: "update_config",
  updates: {
    "trading.trailing_stop_pct": 0.05,        // 3% → 5%
    "trading.risk.daily_loss_limit": 0.05,    // 3% → 5%
    "trading.confidence.auto_execute": 85,    // 80 → 85
  }
})
```

- 변경 즉시 메모리 반영 + `config.yaml` 저장
- 허용 목록 외 키는 무시됨 (보안)

---

## 13. WebSocket 커맨드 레퍼런스

Electron → Python 방향 (모든 커맨드는 JSON 객체)

### 기본 제어

```json
{ "type": "set_auto_trade", "value": true }
{ "type": "set_use_ai",     "value": false }
{ "type": "set_dry_run",    "value": false }
{ "type": "set_market",     "value": "US" }
```

### 주문

```json
{
  "type": "manual_order",
  "ticker": "005930",
  "side": "BUY",
  "price": 75000,
  "qty": 10
}
```

### 캔들 조회

```json
{ "type": "get_candles", "ticker": "005930" }
```

### 종목 관리

```json
{ "type": "add_ticker",    "ticker": "035720" }
{ "type": "remove_ticker", "ticker": "035420" }
```

### 설정 변경

```json
{
  "type": "update_config",
  "updates": {
    "trading.trailing_stop_pct": 0.04
  }
}
```

---

Python → Electron 방향 (브로드캐스트 메시지)

### 메시지 타입

| type | 설명 | 주요 필드 |
|------|------|-----------|
| `tick` | 실시간 가격 | `ticker`, `price`, `volume` |
| `signal` | AI 판단 결과 | `ticker`, `signal`, `confidence`, `reason`, `target_price`, `stop_loss` |
| `order` | 체결 결과 | `ticker`, `side`, `qty`, `price`, `pnl` |
| `balance` | 잔고 현황 | `total_asset`, `today_pnl`, `cash`, `positions[]` |
| `config` | 시스템 상태 | `auto_trade`, `dry_run`, `market`, `tickers[]`, `use_ai` |
| `candles` | OHLCV 데이터 | `ticker`, `ohlcv[]`, `indicators{}` |
| `notification` | 토스트 알림 | `message`, `ntype` |
| `ai_status` | Gemini 사용량 | `remaining`, `daily`, `limit` |
| `performance` | 성과 지표 | `win_rate`, `total_pnl`, `mdd`, `sharpe`, `daily_pnl[]` |

---

## 14. DB 구조

`data/trading.db` (SQLite)

### orders 테이블 — 체결 내역

```sql
SELECT * FROM orders ORDER BY timestamp DESC LIMIT 10;

-- 주요 컬럼
id          INTEGER  PRIMARY KEY
order_id    TEXT     주문 ID (DRY-종목-타임스탬프 or 실제 ID)
ticker      TEXT     종목 코드
side        TEXT     BUY | SELL
price       REAL     체결가
qty         INTEGER  수량
status      TEXT     DRY_RUN | FILLED | rejected 등
signal      TEXT     BUY | SELL | HOLD
confidence  INTEGER  AI 신뢰도 (0~100)
reason      TEXT     AI 판단 근거
pnl         REAL     순손익 (수수료 포함, 매도 시만)
commission  REAL     수수료+세금
dry_run     INTEGER  0=실전, 1=DRY_RUN
timestamp   TEXT     ISO 8601
```

### position_limits 테이블 — 손절가·목표가

```sql
SELECT * FROM position_limits;

ticker        TEXT  PRIMARY KEY
stop_loss     REAL  손절가
target_price  REAL  목표가
updated_at    TEXT  마지막 갱신 시각
```

### ai_signals 테이블 — AI 직전 신호

```sql
SELECT * FROM ai_signals;

ticker      TEXT  PRIMARY KEY
signal      TEXT  BUY | SELL | HOLD
confidence  INTEGER
reason      TEXT
updated_at  TEXT
```

### 유용한 쿼리

```sql
-- 오늘 총 손익
SELECT SUM(pnl) FROM orders
WHERE DATE(timestamp) = DATE('now') AND side = 'SELL';

-- 승률 계산
SELECT
  COUNT(*) as 총거래,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as 승리,
  ROUND(AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) as 승률
FROM orders WHERE side = 'SELL';

-- 종목별 성과
SELECT ticker, SUM(pnl) as 총손익, COUNT(*) as 거래수
FROM orders WHERE side = 'SELL'
GROUP BY ticker ORDER BY 총손익 DESC;
```

---

## 15. 테스트 실행

```bash
# 전체 테스트
python -m pytest tests/test_all.py -v

# 특정 클래스만
python -m pytest tests/test_all.py::TestRiskManager -v
python -m pytest tests/test_all.py::TestBacktest -v
python -m pytest tests/test_all.py::TestTrailingStop -v

# 실패한 테스트만 재실행
python -m pytest tests/test_all.py --lf -v
```

### 테스트 커버리지 (60개)

| 클래스 | 테스트 수 | 커버 내용 |
|--------|-----------|-----------|
| `TestMockAPI` | 8 | Mock API, US float 가격, 잔고 |
| `TestTechnicalAnalyzer` | 9 | RSI/MACD/볼밴, float 정밀도 |
| `TestRiskManager` | 10 | 비중 체크, 쿨다운, force 매도 |
| `TestOrderExecutor` | 8 | 수수료, DB 저장, 성과, AI신호 |
| `TestJudgmentResult` | 3 | float 타입, _safe_hold |
| `TestStopLossTakeProfit` | 3 | 손절/익절 실행, 쿨다운 면제 |
| `TestBacktest` | 7 | 백테스트 결과, KR/US |
| `TestDailyAnalyzer` | 5 | 일봉 지표, 52주 고저 |
| `TestTrailingStop` | 6 | 낙폭 계산, 고점 갱신 |

---

## 16. 실전 전환 체크리스트

> **소액(1~10만원)으로 먼저 테스트하세요.**

```
□ 1. 토스증권 Open API 앱키·시크릿 발급
□ 2. config.yaml의 toss.mock_mode: false 변경
□ 3. toss.app_key, app_secret 실제값 입력
□ 4. config.yaml의 trading.dry_run: false 변경
□ 5. risk.max_position_ratio: 0.05 로 낮춰 시작 (5%)
□ 6. risk.daily_loss_limit: 0.01 로 낮춰 시작 (1%)
□ 7. python main.py --live 실행 시 'YES' 입력 확인
□ 8. UI에서 DRY RUN 토글 OFF (실전 확인 모달)
□ 9. 소액 수동 주문 1건 테스트 → 체결 확인
□ 10. DB orders 테이블에 실제 체결 기록 확인
□ 11. 자동매매 ON 전 잔고·포지션 정상 표시 확인
```

---

## 17. 자주 묻는 질문

**Q. AI가 항상 HOLD만 반환해요**  
A. `config.yaml`의 `gemini.api_key`가 올바른지 확인하세요. 로그에서 "Gemini API 키 미설정" 메시지를 찾아보세요.

**Q. Gemini 한도를 초과했어요**  
A. 앱은 `gemini.daily_limit`(기본 500회)에 닿으면 HOLD를 반환하고 다음 날 초기화됩니다. 종목을 더 추가했다면 `interval_seconds`나 `gemini.min_interval_minutes`를 늘리세요. 실제 무료 한도는 Google 정책에 따라 바뀌니 AI Studio에서 확인하세요.

**Q. 손절가에 도달했는데 매도가 안 됩니다**  
A. 로그에서 해당 종목의 `_sell_triggered` 또는 `잔고 없음` 메시지를 확인하세요. Mock 모드에서 매도 시 `positions`에 해당 종목이 없으면 skipped 됩니다.

**Q. 트레일링 스탑이 너무 민감하게 발동돼요**  
A. `trailing_stop_pct`를 높이세요 (예: 0.03 → 0.05). 런타임 변경도 가능합니다:
```javascript
window.electronAPI.sendCommand({
  type: "update_config",
  updates: { "trading.trailing_stop_pct": 0.05 }
})
```

**Q. 부분 익절을 끄고 싶어요**  
A. `partial_take_profit: 0`으로 설정하면 전량 매도합니다.

**Q. 재시작 후 직전 AI 신호가 사라져요**  
A. 정상적으로 `ai_signals` 테이블에서 복원됩니다. DB 파일(`data/trading.db`)이 삭제되었는지 확인하세요.

**Q. 백테스트 결과를 파일로 저장하고 싶어요**  
A.
```python
import json
result = engine.run(df, ticker="005930")
with open("backtest_result.json", "w") as f:
    json.dump({
        "summary": str(result),
        "total_return_pct": result.total_return_pct,
        "win_rate": result.win_rate,
        "mdd": result.max_drawdown_pct,
        "sharpe": result.sharpe_ratio,
        "trades": [vars(t) for t in result.trades],
        "equity_curve": result.equity_curve,
    }, f, ensure_ascii=False, indent=2)
```

**Q. US 시장 거래 시간은 언제인가요?**  
A. KST 기준 **23:30 ~ 06:00** (자정 걸침). UI 시계가 이 시간대에 초록색으로 표시됩니다.
