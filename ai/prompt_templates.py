"""
AI Judge 프롬프트 템플릿
"""

SYSTEM_PROMPT_KR = """당신은 한국 주식 시장 전문 트레이딩 AI입니다.
기술적 지표를 분석하여 매수/매도/홀딩 신호를 JSON 형식으로 반환합니다.

응답은 반드시 아래 JSON 형식만 출력하세요. 설명이나 마크다운 없이 JSON만.

{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": 0~100,
  "target_price": 목표가(정수),
  "stop_loss": 손절가(정수),
  "target_return_pct": 목표 수익률(소수),
  "stop_loss_pct": 손절 비율(소수, 음수),
  "reason": "판단 근거 (한국어, 2~3문장)",
  "risk_level": "low" | "medium" | "high"
}"""

SYSTEM_PROMPT_US = """당신은 미국 주식 시장 전문 트레이딩 AI입니다.
기술적 지표를 분석하여 매수/매도/홀딩 신호를 JSON 형식으로 반환합니다.

응답은 반드시 아래 JSON 형식만 출력하세요. 설명이나 마크다운 없이 JSON만.

{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": 0~100,
  "target_price": 목표가(소수점 2자리, USD),
  "stop_loss": 손절가(소수점 2자리, USD),
  "target_return_pct": 목표 수익률(소수),
  "stop_loss_pct": 손절 비율(소수, 음수),
  "reason": "판단 근거 (한국어, 2~3문장)",
  "risk_level": "low" | "medium" | "high"
}"""

SYSTEM_PROMPT = SYSTEM_PROMPT_KR


def build_user_prompt(
    ticker: str,
    indicators: dict,
    market: str = "KR",
    position: dict = None,
    prev_signal: str = None,
    prev_confidence: int = None,
    daily_indicators: dict = None,
) -> str:
    """지표 딕셔너리를 사람이 읽기 좋은 프롬프트로 변환합니다.

    Args:
        position: 현재 보유 포지션 {"qty", "avg_price", "current_price"} 또는 None
        prev_signal: 직전 AI 판단 신호 ("BUY"/"SELL"/"HOLD")
        prev_confidence: 직전 AI 판단 신뢰도
    """
    price = indicators.get("current_price", "N/A")
    rsi = indicators.get("rsi")
    macd = indicators.get("macd")
    macd_cross = indicators.get("macd_cross", 0)
    bb_pos = indicators.get("bb_position")
    ma_arr = indicators.get("ma_arrangement", "N/A")
    price_vs_ma20 = indicators.get("price_vs_ma20")
    vol_ratio = indicators.get("volume_ratio")
    support = indicators.get("support")
    resistance = indicators.get("resistance")

    cross_str = {1: "골든크로스 발생", -1: "데드크로스 발생", 0: "크로스 없음"}.get(macd_cross, "N/A")
    bb_pos_str = f"{bb_pos:.2f} (0=하단, 1=상단)" if bb_pos is not None else "N/A"

    if market == "US":
        fmt = lambda v: f"${v:,.2f}" if isinstance(v, (int, float)) else "N/A"
        currency_label = "USD"
    else:
        fmt = lambda v: f"{v:,.0f}원" if isinstance(v, (int, float)) else "N/A"
        currency_label = "KRW"

    # 보유 포지션 섹션
    position_section = ""
    if position and position.get("qty", 0) > 0:
        qty = position["qty"]
        avg = position["avg_price"]
        cur = position.get("current_price") or (price if isinstance(price, (int, float)) else 0)
        pnl_pct = (cur - avg) / avg * 100 if avg > 0 else 0
        position_section = f"""
[현재 보유 포지션]
- 보유 수량: {qty}주
- 평균 매입가: {fmt(avg)}
- 현재 평가손익: {pnl_pct:+.2f}% ({'손실 중' if pnl_pct < 0 else '수익 중'})
"""
    else:
        position_section = "\n[현재 보유 포지션]\n- 미보유 (신규 진입 검토)\n"

    # 직전 AI 판단 섹션
    prev_section = ""
    if prev_signal:
        conf_str = f" (신뢰도 {prev_confidence}%)" if prev_confidence is not None else ""
        prev_section = f"\n[직전 AI 판단]\n- {prev_signal}{conf_str}\n"

    # 일봉 지표 섹션 (멀티 타임프레임)
    daily_section = ""
    if daily_indicators:
        d_ma20   = daily_indicators.get("daily_ma20")
        d_ma60   = daily_indicators.get("daily_ma60")
        d_rsi    = daily_indicators.get("daily_rsi")
        d_trend  = daily_indicators.get("daily_ma_arrangement", "N/A")
        d_vs_ma60 = daily_indicators.get("price_vs_daily_ma60")
        w52_high = daily_indicators.get("week52_high")
        w52_low  = daily_indicators.get("week52_low")
        w52_pos  = daily_indicators.get("week52_position")
        daily_section = f"""
[일봉 지표 (중장기 추세)]
- 일봉 RSI(14): {f'{d_rsi:.1f}' if d_rsi else 'N/A'}
- 일봉 이동평균 배열: {d_trend} (MA60 대비 {f'{d_vs_ma60:+.2f}%' if d_vs_ma60 is not None else 'N/A'})
- 일봉 MA20: {fmt(d_ma20)} / MA60: {fmt(d_ma60)}
- 52주 고점: {fmt(w52_high)} / 저점: {fmt(w52_low)} (현재 위치: {f'{w52_pos:.1f}%' if w52_pos is not None else 'N/A'})
"""

    prompt = f"""종목코드: {ticker} ({currency_label})
현재가: {fmt(price)}
{position_section}{prev_section}{daily_section}
[분봉 기술적 지표 (단기)]
- RSI(14): {f'{rsi:.1f}' if rsi else 'N/A'}
- MACD: {f'{macd:.2f}' if macd else 'N/A'} / {cross_str}
- 볼린저밴드 위치: {bb_pos_str}
- 이동평균 배열: {ma_arr} (MA20 대비 {f'{price_vs_ma20:+.2f}%' if price_vs_ma20 else 'N/A'})
- 거래량 비율: {f'{vol_ratio:.2f}배' if vol_ratio else 'N/A'} (20일 평균 대비)
- 지지선: {fmt(support)}
- 저항선: {fmt(resistance)}

위 지표와 포지션 현황을 종합하여 현재 매매 신호를 JSON으로 반환하세요."""
    return prompt
