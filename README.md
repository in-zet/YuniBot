# 유니 봇 설계서

국제판 CHUNITHM 디스코드 봇 "유니"의 설계 문서 묶음이다. 2026-10-09에 Claude 문서에서 Markdown으로 내보냈다.

- `00-core.md`: 코어 설계서. 구조, 스킬 모듈 표준, 이벤트, 서비스, 권한 태그, 데이터, 기술 스택, 작업 순서, 미확인 사항.
- `skills/`: 스킬마다 문서 하나. 각 문서는 manifest(yaml), 동작, 도구, 이벤트, 데이터, 설정, 유니 지침, 테스트, 미확인 사항 순서다.

| 스킬 | 분류 |
| --- | --- |
| chunithm-account | 계정 연결 |
| account-link | 계정 연결 |
| chunithm-song | 곡 정보 |
| chunithm-border | 곡 정보 |
| chunithm-terms | 질문 답변 |
| chunithm-sync | 기록과 리포트 |
| chunithm-report | 기록과 리포트 |
| chunithm-goals | 목표 |
| chunithm-session | 세션과 후기 |
| chunithm-arcade | 오락실 |
| chunithm-notes | 메모 |
| chunithm-updates | 알림 |
| web-qa | 질문 답변 |
| memory | 메모리 |
| yuni-bond | 함께한 기록 |
| admin | 관리 |

문서 안의 claude.ai 링크는 원본 문서를 가리킨다. 저장소에서는 이 폴더를 `docs/design/`에 두는 것을 전제로 한다.
