# 자동매매 시스템 개선 TODO

> 항목별로 구현 → 루프 검증 → 완료 순서로 진행  
> 마지막 업데이트: 2026-05-20

---

## 🔴 버그 수정 (즉시)

- [x] **#1** `US 주식 int 캐스팅 제거`
  - `ai/judgment_engine.py` → `target_price`, `stop_loss` float 유지
  - `analyzer/technical.py` → `current_price` float 유지
  - `executor/order.py` → KR은 int, US는 float 분기
  - `ai/prompt_templates.py` → 가격 포맷 검증

- [x] **#2** `손절 매도 쿨다운 면제`
  - `executor/risk_manager.py` → `check_sell()` 에 `force=False` 파라미터 추가
  - `executor/order.py` → `execute_stop_loss()` 에서 `force=True` 전달
  - 손절은 쿨다운 중에도 무조건 실행

- [x] **#3** `테스트 보강`
  - `tests/test_all.py` → 신규 메서드 커버리지 추가
    - `check_sell()`, `execute_stop_loss()`, `execute_take_profit()`
    - `get_today_pnl()`, `_commission()`, `save/load/delete_position_limit()`
    - US 주식 float 가격 처리 검증

---

## 🟡 핵심 기능 추가

- [x] **#4** `트레일링 스탑`
  - `main.py` → `_trailing_stop: dict[str, float]` (ticker → 고점 추적)
  - 틱마다 고점 갱신, (고점 - 현재가) / 고점 >= trailing_pct 시 매도
  - `config.yaml` → `trading.trailing_stop_pct: 0.03` (3%)
  - UI → 트레일링 스탑 뱃지 표시

- [x] **#5** `부분 익절`
  - `config.yaml` → `trading.partial_take_profit: 0.5` (목표가 도달 시 50% 매도)
  - `executor/order.py` → `execute_take_profit()` qty 파라미터 추가
  - `main.py` → 부분 매도 후 나머지 포지션 계속 모니터링
  - 2차 목표가(target_price * 1.05 등) 설정 가능하도록

- [x] **#6** `AI 직전 신호 DB 저장`
  - `executor/order.py` → `orders` 테이블에서 마지막 BUY 신호 조회 또는
  - 별도 `ai_signals` 테이블 추가 (ticker, signal, confidence, timestamp)
  - `main.py` → 시작 시 DB에서 `_last_signals` 복원
  - `ai/judgment_engine.py` → 신호 저장 훅 또는 main에서 저장

---

## 🟡 전략 고도화

- [x] **#7** `멀티 타임프레임`
  - `collector/websocket_client.py` → 일봉 캔들(D) 별도 수집
  - `analyzer/technical.py` → `analyze_daily(candles_daily)` 추가
    - 일봉 MA20/60 배열, 일봉 RSI, 52주 고점/저점 대비 위치
  - `ai/prompt_templates.py` → 일봉 지표 섹션 추가
  - `main.py` → 일봉 데이터 주기적 갱신 (30분마다)

- [x] **#8** `성과 분석`
  - `executor/order.py` → `get_performance()` 메서드 추가
    - 승률, 총 손익, 평균 수익/손실, MDD, 샤프 지수(근사)
  - UI → "성과" 탭 또는 패널 추가
    - 누적 손익 미니 차트 (7일/30일)
    - 종목별 승률 테이블

- [x] **#9** `백테스트`
  - `backtest/` 폴더 신설
    - `engine.py` → 과거 캔들 데이터로 전략 시뮬레이션
    - `data_loader.py` → CSV / Yahoo Finance 데이터 로드
  - AI 판단 없이 기술적 지표 기반 룰 백테스트 먼저 구현
  - 결과: 기간 수익률, 최대 낙폭, 거래 횟수

---

## 🟡 편의성

- [x] **#10** `UI 종목 관리`
  - UI → 종목 추가/삭제 인풋 + 버튼
  - `main.py` → `add_ticker` / `remove_ticker` 커맨드 처리
  - 변경 시 `config.yaml` 자동 업데이트 + 콜렉터 재시작

- [x] **#11** `설정 파라미터 UI`
  - UI → "설정" 패널 (슬라이더/인풋)
    - 리스크 비율 (max_position_ratio, daily_loss_limit)
    - 트레일링 스탑 % (추가 후)
    - 부분 익절 % (추가 후)
    - Gemini 모델 선택
  - `main.py` → `update_config` 커맨드 처리
  - `config_loader.py` → 런타임 설정 변경 + YAML 저장 지원

---

## 진행 상황

| # | 항목 | 상태 | 완료일 |
|---|------|------|--------|
| 1 | US float 캐스팅 | ✅ 완료 | 2026-05-20 |
| 2 | 손절 쿨다운 면제 | ✅ 완료 | 2026-05-20 |
| 3 | 테스트 보강 | ✅ 완료 | 2026-05-20 |
| 4 | 트레일링 스탑 | ✅ 완료 | 2026-05-20 |
| 5 | 부분 익절 | ✅ 완료 | 2026-05-20 |
| 6 | AI 신호 DB 저장 | ✅ 완료 | 2026-05-20 |
| 7 | 멀티 타임프레임 | ✅ 완료 | 2026-05-20 |
| 8 | 성과 분석 | ✅ 완료 | 2026-05-20 |
| 9 | 백테스트 | ✅ 완료 | 2026-05-20 |
| 10 | UI 종목 관리 (백엔드) | ✅ 완료 | 2026-05-20 |
| 11 | 설정 파라미터 (백엔드) | ✅ 완료 | 2026-05-20 |
