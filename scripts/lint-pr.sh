#!/usr/bin/env bash
# PR 이름·본문 규칙 검사 (docs/CONVENTIONS.md)
# 사용법: PR_TITLE=... PR_BRANCH=... PR_BODY=... scripts/lint-pr.sh
set -uo pipefail
TYPES="feat|fix|perf|refactor|chore|security|test|docs|ci|revert"
SCOPES="collector|detector|react|llm|notifier|exporter|core|repo"
fail=0
err() { echo "::error title=PR 규칙 위반::$1"; fail=1; }

[[ "${PR_TITLE}" =~ ^($TYPES)\(($SCOPES)\):\ .+ ]] \
  || err "제목 형식: '<type>(<scope>): <한글 요약>' — 현재: '${PR_TITLE}'"
[[ "${PR_BRANCH}" =~ ^($TYPES)/($SCOPES)-[a-z0-9]+(-[a-z0-9]+)*$ ]] \
  || err "브랜치 형식: '<type>/<scope>-<짧은-설명>' — 현재: '${PR_BRANCH}'"
for h in "## 배경" "## 변경 사항" "## 영향 범위" "## 검증" "## 롤백" "## 관련"; do
  grep -qF "$h" <<<"${PR_BODY}" || err "본문 섹션 누락: '$h'"
done
[[ $fail -eq 0 ]] && echo "PR 규칙 통과"
exit $fail
