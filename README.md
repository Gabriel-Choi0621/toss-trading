# 🤖 Toss Trading — AI 자동매매 시스템

토스증권 오픈 API 기반 AI 자동매매 시스템입니다.
Gemini AI가 기술적 지표를 분석해 매수/매도 신호를 생성하고, Electron 데스크탑 앱으로 실시간 모니터링합니다.

---

## 📋 목차

1. [시작 전 필요한 것](#1-시작-전-필요한-것)
2. [API 키 발급 가이드](#2-api-키-발급-가이드)
3. [설치 및 실행](#3-설치-및-실행)
4. [Electron 앱 실행](#4-electron-앱-실행)
5. [토스증권 API 연동 (공개 후)](#5-토스증권-api-연동-공개-후)
6. [파일 구조 설명](#6-파일-구조-설명)
7. [자주 묻는 질문](#7-자주-묻는-질문)

---

## 1. 시작 전 필요한 것

| 항목 | 버전 | 용도 |
|------|------|------|
| Python | 3.11 이상 | 매매 시스템 백엔드 |
| Node.js | 18 이상 | Electron 데스크탑 앱 |
| Gemini API 키 | 무료 | AI 매매 판단 |
| 토스증권 API 키 | 공개 후 발급 | 실제 주문 실행 |

> **현재 토스증권 API는 미공개 상태입니다.**
> API 공개 전까지는 Mock 데이터로 시스템 전체를 테스트할 수 있습니다.

---

## 2. API 키 발급 가이드

### 🟢 Gemini API (무료 · 필수)

AI가 기술적 지표를 보고 매수/매도/홀딩을 판단하는 데 사용합니다.

**무료 티어 한도**
- 하루 250회 요청
- 분당 10회 요청
- 장중(09:00~15:30) 5분 간격으로 3종목 = 하루 약 234회 → **무료 한도 내**

**발급 방법**

1. [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey) 접속
2. Google 계정으로 로그인 (없으면 무료 가입)
3. **"Create API Key"** 버튼 클릭
4. 프로젝트 선택 후 키 복사
5. `config.yaml`에 입력:

```yaml
gemini:
  api_key: "여기에_발급받은_키_붙여넣기"
```

> 카드 등록 없이 바로 무료로 사용 가능합니다.

---

### 🔵 토스증권 API (공개 후 · 실전 매매 필수)

실제 주문 실행에 사용합니다.
**현재 미공개 상태이며, 공개되면 이 문서를 업데이트할 예정입니다.**

**공개 후 발급 방법 (예상)**

1. 토스증권 앱 → 설정 → 오픈 API 신청
2. App Key / App Secret 발급
3. `config.yaml`에 입력:

```yaml
toss:
  app_key: "발급받은_앱키"
  app_secret: "발급받은_시크릿"
  account_no: "계좌번호"
  mock_mode: false   # 실전 전환 시 false로 변경
```

> ⚠️ `mock_mode: false`로 바꾸기 전에 반드시 `dry_run: true` 상태에서 충분히 테스트하세요.

---

### 📱 텔레그램 봇 (선택사항)

신뢰도 60~79% 신호를 받았을 때 텔레그램으로 알림을 받고 직접 매수/거절을 선택할 수 있습니다.

**발급 방법**

1. 텔레그램에서 **@BotFather** 검색
2. `/newbot` 명령어 입력
3. 봇 이름 설정 후 **Bot Token** 복사
4. 본인 텔레그램에서 **@userinfobot** 검색 → `/start` → **Chat ID** 확인
5. `config.yaml`에 입력:

```yaml
telegram:
  bot_token: "발급받은_봇_토큰"
  chat_id: "본인_채팅_ID"
  enabled: true
```

> 텔레그램 봇 기능은 현재 개발 예정 상태입니다 (`notification/` 폴더).

---

## 3. 설치 및 실행

### Python 패키지 설치

```bash
pip install pandas numpy pandas-ta google-generativeai pyyaml aiohttp websockets
```

### config.yaml 설정

```yaml
gemini:
  api_key: "YOUR_GEMINI_API_KEY"   # 위에서 발급한 키

trading:
  dry_run: true                     # 처음엔 반드시 true!
  tickers:
    - "005930"  # 삼성전자
    - "000660"  # SK하이닉스
    - "035420"  # NAVER
  interval_seconds: 300             # 5분 간격 (무료 한도 맞춤)
```

### 실행

```bash
# 모의 실행 (기본, 실제 주문 없음)
python main.py

# 실전 실행 (토스증권 API 공개 + 충분한 테스트 후)
python main.py --live
```

---

## 4. Electron 앱 실행

Python을 먼저 실행한 뒤 Electron 앱을 켜면 실시간 대시보드가 연결됩니다.

```bash
# 터미널 1 — Python 백엔드
python main.py

# 터미널 2 — Electron 앱
cd electron
npm install    # 최초 1회만
npm start
```

**앱 기능**

| 버튼 | 동작 |
|------|------|
| 🔴 빨간 버튼 | 트레이로 숨기기 |
| 🟡 노란 버튼 | 최소화 |
| 🟢 초록 버튼 | HUD 미니 모드 (작은 플로팅 위젯) |
| 트레이 우클릭 | 항상 위에 표시 / 종료 |

**배포용 앱 빌드**

```bash
cd electron
npm run build-mac   # macOS .dmg
npm run build-win   # Windows .exe
```

---

## 5. 토스증권 API 연동 (공개 후)

API가 공개되면 **`collector/toss_api.py` 파일 하나만** 수정하면 됩니다.
나머지 코드(분석, AI 판단, UI)는 전혀 건드릴 필요 없어요.

파일 안에 `# TODO: 실제 REST 구현` 주석이 달린 곳 6군데를 채워넣으면 됩니다:

| 메서드 | 내용 |
|--------|------|
| `get_current_price()` | 현재가 조회 |
| `get_ohlcv()` | OHLCV(캔들) 데이터 조회 |
| `get_balance()` | 잔고 조회 |
| `place_order()` | 주문 실행 |
| `cancel_order()` | 주문 취소 |
| `subscribe_realtime()` | WebSocket 실시간 시세 |

구현 완료 후 `config.yaml`에서:

```yaml
toss:
  mock_mode: false  # 이것만 바꾸면 끝
```

---

## 6. 파일 구조 설명

```
toss-trading/
│
├── main.py                  # 진입점 — 모든 모듈을 연결해 실행
├── config.yaml              # ⚙️ 설정 파일 (API 키 여기 입력)
├── config_loader.py         # config.yaml 로더 유틸
├── ui_server.py             # Python ↔ Electron WebSocket 브릿지
├── requirements.txt         # Python 패키지 목록
│
├── collector/
│   ├── toss_api.py          # 토스증권 API 인터페이스 (현재 Mock)
│   └── websocket_client.py  # 실시간 데이터 수집 + 캔들 버퍼
│
├── analyzer/
│   └── technical.py         # RSI, MACD, 볼린저밴드 등 지표 계산
│
├── ai/
│   ├── judgment_engine.py   # Gemini API 호출 + 무료 한도 관리
│   └── prompt_templates.py  # AI에게 보내는 프롬프트
│
├── executor/
│   ├── order.py             # 주문 실행 + SQLite 기록
│   └── risk_manager.py      # 리스크 체크 (비중/손실/쿨다운)
│
├── notification/            # 텔레그램 봇 (개발 예정)
│
├── electron/                # 데스크탑 앱
│   ├── package.json
│   └── src/
│       ├── main.js          # Electron 메인 프로세스
│       ├── preload.js       # 보안 브릿지
│       └── index.html       # 대시보드 UI
│
├── data/                    # SQLite DB 저장 위치
├── logs/                    # 로그 파일
└── tests/
    └── test_all.py          # 테스트 코드
```

---

## 7. 자주 묻는 질문

**Q. 실제 돈이 나가나요?**
> `config.yaml`의 `dry_run: true`가 기본값입니다. 이 상태에서는 주문이 실행되지 않고 로그만 남습니다. 실전 전환은 충분한 테스트 후에 하세요.

**Q. Gemini API 비용이 얼마나 나오나요?**
> 장중 5분 간격, 3종목 기준 하루 약 234회 호출로 무료 한도(250회) 안에 들어옵니다. 한도 초과 시 자동으로 HOLD를 반환하고 다음 날 초기화됩니다.

**Q. 토스증권 API가 없는데 테스트할 수 있나요?**
> 네. `mock_mode: true`(기본값)로 설정하면 랜덤 Mock 데이터로 시스템 전체를 테스트할 수 있습니다.

**Q. 어디에 API 키를 보관하나요?**
> `config.yaml`에만 입력하세요. 이 파일은 `.gitignore`에 포함되어 있어 Git에 올라가지 않습니다. 절대 코드에 직접 입력하지 마세요.

**Q. Python과 Node.js 버전은 어떻게 확인하나요?**
> 터미널에서 `python --version`, `node --version`으로 확인할 수 있습니다.
