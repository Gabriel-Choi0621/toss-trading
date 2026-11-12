# Electron 앱 실행 가이드

## 구조

```
python main.py          →   WebSocket 서버 (ws://localhost:8765)
                                    ↕
electron/               →   데스크탑 앱 (UI)
```

---

## 1단계 — Python 백엔드 실행

```bash
# 프로젝트 루트에서
pip install pandas numpy pandas-ta anthropic pyyaml websockets

python main.py          # dry-run (기본)
python main.py --live   # 실전 (주의!)
```

---

## 2단계 — Electron 앱 실행

```bash
cd electron
npm install
npm start
```

처음 실행하면 Node.js가 Electron을 다운로드합니다 (약 200MB, 최초 1회).

---

## 앱 사용법

| 버튼 | 동작 |
|------|------|
| 🔴 빨간 버튼 | 트레이로 숨기기 (종료 아님) |
| 🟡 노란 버튼 | 최소화 |
| 🟢 초록 버튼 | HUD 미니 모드 전환 |
| 트레이 우클릭 | 메뉴 (항상 위에, HUD 모드, 종료) |

---

## HUD 미니 모드

초록 버튼 클릭 → 작은 플로팅 위젯으로 전환  
화면 한쪽 구석에 항상 떠있으면서 신호만 확인 가능

---

## 패키징 (배포용 앱 만들기)

```bash
cd electron
npm run build-mac   # .dmg (macOS)
npm run build-win   # .exe (Windows)
```

빌드 결과물은 `electron/dist/` 폴더에 생성됩니다.

---

## 문제 해결

**"연결 중..." 상태가 계속 보여요**  
→ `python main.py`가 실행 중인지 확인하세요

**포트 충돌 오류**  
→ `ui_server.py`에서 port를 `8766`으로 변경하고  
  `electron/src/main.js`의 `WS_URL`도 동일하게 변경

**Node.js 없어요**  
→ https://nodejs.org 에서 LTS 버전 설치
