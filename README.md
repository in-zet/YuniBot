# YuniBot

국제판 CHUNITHM을 하는 지인 디스코드 서버용 봇 "유니"다. 유니를 멘션하면 LLM(Gemini 또는 Claude)이 스킬 도구를 골라 답하고, 슬래시 명령은 LLM 없이 같은 기능을 쓴다. CHUNITHM-NET 접속·파싱은 [chuni-penguin](https://github.com/beer-psi/chuni-penguin)(0BSD)에서 가져온 코드를 쓴다.

## 실행

```bash
cp .env.example .env        # 토큰·키·암호화 키를 채운다
uv sync
uv run pytest -q
docker compose up -d --build
```

## 문서

- 설계: `docs/design/00-core.md`(코어), `docs/design/skills/`(스킬 16개)
- 진행 상태·범위: `docs/plan.md`
- 개발 규칙: `docs/conventions.md`
- Claude Code로 작업할 때: `CLAUDE.md`
