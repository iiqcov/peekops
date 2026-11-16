# CLAUDE.md

이 저장소는 홈서버에서 실제로 쓰는 경량 AIOps 진단 도구다. 지표를 수집해 임계치로 이상을 감지하고, 로컬 LLM이 ReAct 루프로 조회 명령을 실행하며 원인을 추적한 뒤 Discord · Prometheus로 내보낸다.

작업 전에 반드시 `docs/CONVENTIONS.md`를 읽고 따른다. 핵심만 요약하면:

- `main`에 직접 push 금지. 브랜치 → PR → 사람이 Squash merge
- 브랜치: `<type>/<scope>-<짧은-설명>` (예: `fix/llm-strip-think-block`)
- PR 제목: `<type>(<scope>): <한글 요약>`
- PR 본문: `.github/pull_request_template.md`의 모든 섹션을 채운다 (배경 / 변경 사항 / 영향 범위 / 검증 / 롤백 / 관련 / Notes)
- PR · Issue · 댓글은 **개조식**(명사형 종결: 발생, 확인, 추정, 수행 …)으로 작성. 서술형 문장 금지
- 운영 중 발견한 문제는 `bug` Issue로 기록하고 PR에서 `Closes #n`으로 연결
- 동작을 바꾸면 테스트를 함께 추가하고, push 전에 `python -m pytest` 실행
- LLM이 실행하는 명령어 경로(`analyzers/react.py`의 검사 · 실행)를 바꾸면 `security` 라벨 + 우회 시도 테스트 추가
- 웹훅 URL · API 키는 커밋 금지 (`.env` 또는 환경변수)
- 홈서버에 직접 접근할 수 없으면 확인 명령을 제시하고, 결과를 받아 PR/Issue에 남긴다
