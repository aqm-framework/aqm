# aqm &nbsp;|&nbsp; [English](../README.md)

**AI 에이전트 팀을 YAML로 구축. 코드 없이. API 키 없이.**

여러 AI 에이전트가 큐를 통해 태스크를 전달하거나, 세션에서 합의에 도달할 때까지 토론하는 오케스트레이션 프레임워크.

```
[사용자] → [기획자] → [리뷰어] ──승인──► [세션] → [구현자]
              ▲           │            [아키][보안][FE]
              └── 반려 ──┘             합의까지 토론
```

## 왜 aqm?

| 문제 | 단일 에이전트 | aqm |
|---|---|---|
| 같은 LLM이 자기 코드를 리뷰 | 편향 1개 | **교차 LLM 검증** |
| 품질 검사 없음 | 스스로 "좋다" 판단 | **품질 게이트** 자동 거부 |
| 컨텍스트 폭발 | 모든 것을 하나의 대화에 | **5가지 전략** (55-85% 절감) |
| 프로세스 표준화 불가 | 즉흥 실행 | **YAML 파이프라인** |
| API 비용 | 토큰당 과금 | **CLI 기반** (추가 비용 없음) |

## 설치

```bash
pip install aqm
```

> Python 3.11+. LLM CLI 최소 하나 필요: `claude`, `gemini`, `codex`

## 빠른 시작

```bash
aqm init && aqm run "JWT 인증 추가" && aqm serve
```

## 예제

### 코드 리뷰 파이프라인

```yaml
agents:
  - id: developer
    runtime: claude
    system_prompt: "구현: {{ input }}"
    handoffs: [{ to: reviewer }]

  - id: reviewer
    runtime: gemini
    gate: { type: llm, prompt: "프로덕션 준비?", max_retries: 3 }
    handoffs:
      - { to: qa, condition: on_approve }
      - { to: developer, condition: on_reject }

  - id: qa
    runtime: claude
    context_strategy: last_only
    system_prompt: "테스트 작성: {{ input }}"
```

### 아키텍처 세션

```yaml
- id: design_session
  type: session
  participants: [architect, security]
  consensus: { method: vote, keyword: "VOTE: AGREE", require: all }
  summary_agent: architect
```

### 사람 승인 배포

```yaml
- id: deployer
  gate: { type: human }    # 수동 승인 전까지 일시정지
```

## 주요 기능

| 기능 | 설명 |
|------|------|
| **멀티 LLM** | 에이전트별 Claude/Gemini/Codex 혼합 |
| **세션** | 라운드 로빈 토론, 투표 합의, 중재자 모드 |
| **청크 분해** | `CHUNK_ADD/DONE/REMOVE` 지시어로 작업 단위 추적 |
| **컨텍스트 전략** | `both`/`shared`/`last_only`/`own`/`none` (55-85% 토큰 절감) |
| **핸드오프** | 고정/팬아웃/에이전트 결정 라우팅 |
| **사람 입력** | `before`/`on_demand`/`both` 모드 |
| **게이트** | LLM 자동 평가 + Human 수동 승인 |
| **재시작** | Stage 스냅샷 → 실패 지점에서 복구 |
| **MCP 서버** | GitHub, 파일시스템, 커스텀 도구 연결 |
| **파라미터** | `${{ params.X }}` 변수 + CLI 오버라이드 |
| **상속** | `abstract` + `extends`로 에이전트 템플릿 |
| **레지스트리** | 시맨틱 버전 관리, `pull name@1.0.0`, 커뮤니티 공유 |
| **웹 대시보드** | 사이드바 네비게이션, 비주얼 에이전트 에디터, 실시간 SSE |

## CLI 레퍼런스

```bash
# 설정
aqm init                              # 대화형 설정
aqm validate                          # YAML 검증
aqm agents                            # 에이전트 그래프

# 실행
aqm run "JWT 인증 추가"                # 파이프라인 실행
aqm run "태스크" --agent dev --param model=opus

# 관리
aqm list / status / cancel / fix / restart
aqm restart T-ABC123 --from-stage 2   # 특정 stage 재시작

# 게이트 & 입력
aqm approve / reject / human-input

# 청크
aqm chunks list / done / add / remove

# 파이프라인
aqm pipeline list / create / default / versions

# 레지스트리
aqm search "code review"              # 검색
aqm pull code-review@1.0.0            # 특정 버전 설치
aqm publish --version 2.0.0           # 버전 지정 공유

# 대시보드
aqm serve                             # 웹 UI (localhost:8000)
```

## agents.yaml 레퍼런스

### 에이전트 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | `string` | 고유 식별자 (필수) |
| `runtime` | `claude`/`gemini`/`codex` | LLM 런타임 |
| `system_prompt` | `string` | Jinja2 템플릿 (`{{ input }}`, `{{ context }}`) |
| `handoffs` | `list` | `to`, `condition`, `payload` |
| `gate` | `object` | `type: llm/human`, `prompt`, `max_retries` |
| `context_strategy` | `string` | `both`/`shared`/`last_only`/`own`/`none` |
| `mcp` | `list` | MCP 서버 연결 |
| `human_input` | `bool/object` | `before`/`on_demand`/`both` |

### 세션 필드 (`type: session`)

| 필드 | 설명 |
|---|---|
| `participants` | 에이전트 ID 목록 |
| `consensus` | `method`(vote/moderator), `keyword`, `require`(all/majority) |
| `max_rounds` / `summary_agent` / `chunks` | 선택사항 |

## 비교

| | LangGraph | CrewAI | AutoGen | aqm |
|---|---|---|---|---|
| 정의 | Python | Python+YAML | Python | **YAML만** |
| 레지스트리 | ❌ | 유료 | ❌ | **오픈** |
| 멀티에이전트 토론 | ❌ | ❌ | 그룹챗 | **세션 + 합의** |
| 컨텍스트 최적화 | ❌ | 자동요약 | ❌ | **5전략** |
| 비용 | API | API | API | **CLI 구독** |
| 대시보드 | 유료 | 유료 | ❌ | **내장** |

## 커뮤니티

**[Discord](https://discord.gg/798f3rED)** | **[레지스트리](https://github.com/aqm-framework/registry)** | **[JSON 스키마](../schema/agents-schema.json)**

## 라이선스

MIT
