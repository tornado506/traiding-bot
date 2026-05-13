"""
FinalSniperBotV6 - 클로드 3차 검수 최종 수정판
══════════════════════════════════════════════════
[이전 버전 반영 확인]
  ✅ Bug1: continue → processed_be 플래그 교체 (XAUUSDT 스킵 방지)
  ✅ Bug2: start_price 재시작 복원 (get_executions 기반)
  ✅ Bug3: 헷지 종료 qty float 변환 + round 처리
  ✅ Bug4: sleep(3) 후 포지션 재조회 반영
  ✅ Improvement: calc_step 여유치 0.95 상향

[이번 버전 신규 수정]
  🔴 Fix-B: start_price 복원 시 이전 거래 오염 방지
            반대 방향 체결(익절) 발견 즉시 탐색 중단으로
            현재 포지션 체결가만 정확히 필터링

[확인 사항]
  ℹ️  헷지 주문: Bybit V5는 orderType="Market" + triggerPrice 조합이
      조건부 스탑 주문으로 처리됨 (별도 "StopMarket" 타입 없음) → 원본 유지
"""

print("Step 1: 프로그램 로딩 시작...")
import time
import logging
import numpy as np
import pandas as pd
import requests
print("Step 2: 라이브러리 로드 완료")
from pybit.unified_trading import HTTP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY = "O1QpHJH4wdsjPiCe1x"
API_SECRET = "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
TELEGRAM_TOKEN = "8750597756:AAHRCFpsftkHRZErKh10G1xW8PmawwXieGQ"
CHAT_ID = "6931693139"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [2] 오승연 계정 
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY        = "MZxVDs6SlVHsDndKXE"
API_SECRET     = "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
TELEGRAM_TOKEN = ""  
CHAT_ID        = ""  

