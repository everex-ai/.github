# Task

## 구역
- 사람 구역: 진행 배경, 예상 산출물 (Task 완료 기준)
- agent 구역: 결과 산출물 (Task 완료 기준과 1:1 대응), TL;DR

## 완료 판단 기준
precheck.json의 ac 목록(AC-1, AC-2, ...)이 기준이다. 항목마다 comment, sub-task, PR에서 근거를 찾아
달성(✅) 또는 미달성(❌)을 정한다. 근거가 없는 항목은 달성으로 쓰지 않는다.
결과 산출물의 항목 수와 번호는 ac 목록과 정확히 같아야 한다(T3). 빠뜨리거나 새 번호를 만들지 않는다.

## deliverables.wiki 형식 (결과 산출물 구역 본문. h2 제목 줄은 넣지 않는다)
```
* AC-1 (산출물 이름) / ✅ 달성
** 근거: [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42] (2026-09-11 병합)
* AC-2 (산출물 이름) / ❌ 미달성 (사유: 평가 리포트 링크 없음)
** 필요한 것: 평가 리포트 링크를 comment로 남길 것
* AC-3 (산출물 이름) / ✅ 달성
** 근거: [comment 2026-09-12|https://<site>/browse/INNO-17?focusedCommentId=10123] 배포 태그 v1.4.2
```

## tldr.wiki 형식
```
h2. Summary
* (무엇을 했는지 한 줄)
* (핵심 결과 1: 수치로. 예: val set PCK@0.2 0.83, baseline 대비 +0.04)
* (핵심 결과 2 또는 산출물)
* {color:#6b778c}상태: <issue.json의 status>, 판정: <통과|보완 요청|보류>, 갱신: YYYY-MM-DD HH:MM{color}
```
정리 모드에서는 "판정" 부분을 빼고 "상태"와 "갱신"만 쓴다.

## verdict.json의 acStatus
ac 목록의 항목마다 하나씩 쓴다.
```json
{ "id": "AC-1", "done": true,  "evidence": "https://github.com/everex-ai/repo/pull/42" }
{ "id": "AC-2", "done": false, "reason": "평가 리포트 링크 없음" }
```

## 판정
- pass: 모든 AC가 ✅이고 R2(링크), R4, A2 등 필수 검사가 통과
- fix: ❌인 AC가 있거나 T1, T2, R4가 fail
- escalate: AC가 모호함, 담당자가 ❌ 항목을 정당한 사유로 종료하려 함, A2가 fail
