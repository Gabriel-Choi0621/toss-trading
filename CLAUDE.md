# CLAUDE.md

## 프로젝트 개요
토스증권 오픈 API 기반 AI 자동매매 시스템.
**Mock 단계 프로토타입** — 개발 당시 토스증권 API가 공개 전이라 `collector/toss_api.py`의 `TossAPIInterface`를 Mock으로 구현함.
실제 연동 시 해당 클래스의 `mock=False` 분기를 구현하면 됨.

## 디렉토리 구조
```
toss-trading/
├── main.py                  # 오케스트레이터 (진입점)
├── config.yaml              # 설정 파일 (Git 제외)
├── config_loader.py         # 설정 로더 유틸
├── collector/
│   ├── toss_api.py          # 토스증권 API 인터페이스 (현재 Mock)
│   └── websocket_client.py  # 데이터 수집기
├── analyzer/
│   └── technical.py         # 기술적 지표 계산
├── ai/
│   ├── judgment_engine.py   # Gemini 판단 엔진 (기본 gemini-3.1-flash-lite)
│   └── prompt_templates.py  # 프롬프트 관리
├── executor/
│   ├── order.py             # 주문 실행 + DB 저장
│   └── risk_manager.py      # 리스크 체크
├── notification/            # 텔레그램 봇 (미구현)
├── data/                    # SQLite DB
├── logs/                    # 로그 파일
└── tests/
    └── test_all.py          # 테스트 스위트
```

## 기술 스택
- Python 3.11+
- pandas, numpy, pandas-ta (TA-Lib 대체)
- google-genai (기본 모델 gemini-3.1-flash-lite, 앱 내부 한도 500회/일 · 15회/분)
- python-telegram-bot (미구현)
- SQLite (표준 라이브러리)
- asyncio 기반

## 코딩 컨벤션
- 타입 힌트 필수
- docstring 작성 (모듈/클래스/주요 함수)
- 에러는 logging으로 처리, 치명적 오류만 raise
- 금액 계산 시 Decimal 고려

## API 키 관리
- 모든 키는 `config.yaml`에서 로드 (`config_loader.get()` 사용)
- 절대 하드코딩 금지
- `config.yaml`은 `.gitignore`에 포함됨

## 주의사항
- `dry_run=True`가 기본값 — 실전 전환 시 반드시 소액 테스트 먼저
- 실제 API 연동 구현 전까지 `toss.mock_mode: true` 유지
- 텔레그램 봇은 `notification/` 폴더에 추가 예정 (미구현)
