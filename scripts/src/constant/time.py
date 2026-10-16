"""시간대 상수."""

from __future__ import annotations

import datetime as dt

# 한국 시간. runs의 at, 토큰(인증 키) 만료일, 기간 경계, ISO 주, 월 계산에 사용
KST = dt.timezone(dt.timedelta(hours=9))
