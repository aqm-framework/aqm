# aqm &nbsp;|&nbsp; [English](../README.md)

**AI 에이전트 팀을 YAML로 구축. 코드 없이. API 키 없이. 파이프라인만으로.**

여러 AI 에이전트가 **명시적 큐**를 통해 태스크를 전달하거나, **실시간 세션**에서 합의에 도달할 때까지 토론하는 오케스트레이션 프레임워크입니다. 한 번 정의하고, 어디서든 실행하고, 누구와든 공유하세요.

```
  [사용자] ──입력──► [기획자] ──► [리뷰어] ──승인──► [설계 세션] ──► [구현자]
                        ▲              │               ┌──┬──┬──┐
                        └── 반려 ──────┘               ▼  ▼  ▼  ▼  라운드 로빈
                        └── 질문 ──►[사용자]          [아키][보안][FE]  합의까지
```

## 왜 aqm인가?

단일 AI 에이전트는 자기가 쓴 코드를 **같은 편향**으로 리뷰합니다. 자기 자신의 맹점을 잡을 수 없습니다.

aqm은 **팀**을 제공합니다 — 각 에이전트는 전용 역할, 별도 프롬프트, 그리고 선택적으로 다른 LLM을 가집니다. 품질 게이트가 나쁜 결과물을 자동으로 거부합니다. 세션에서 에이전트들이 결정 전에 토론합니다.

```yaml
# YAML 파일 하나. 이것이 전체 파이프라인입니다.
agents:
  - id: developer
    runtime: claude
    system_prompt: "구현: {{ input }}"
    handoffs: [{ to: reviewer }]

  - id: reviewer
    runtime: gemini                    # 다른 LLM이 다른 버그를 잡음
    system_prompt: "보안 리뷰: {{ input }}"
    gate:
      type: llm
      prompt: "프로덕션 준비가 되었나요?"
      max_retries: 3                   # 자동 거부 → 최대 3번 재시도
    handoffs:
      - { to: deployer, condition: on_approve }
      - { to: developer, condition: on_reject }

  - id: deployer
    runtime: claude
    context_strategy: none             # 85% 토큰 절감 — 컨텍스트 불필요
    system_prompt: "배포: {{ input }}"
```

```bash
pip install aqm && aqm init && aqm run "JWT 인증 추가"
```

### aqm이 다른 점

| 문제 | 단일 에이전트 | aqm |
|---|---|---|
| 같은 LLM이 자기 코드를 리뷰 | 편향 1개, 관점 1개 | **교차 LLM 검증** (Claude가 작성, Gemini가 리뷰) |
| 강제 품질 검사 없음 | 에이전트가 스스로 "좋아 보인다" 판단 | **품질 게이트**가 자동 거부 후 재시도 |
| 규모 커지면 컨텍스트 윈도우 폭발 | 하나의 대화에 모든 것 | **5가지 컨텍스트 전략** — 55-85% 토큰 절감 |
| 팀 프로세스 표준화 불가 | 매번 즉흥적 실행 | **YAML 파이프라인** — 버전 관리, 공유 가능 |
| 복잡한 작업의 진행 상황 추적 불가 | 내장 작업 추적 없음 | **청크 분해** — 에이전트가 작업을 추적 가능한 단위로 분리 |
| 비싼 API 비용 | 토큰당 API 과금 누적 | **CLI 기반** — 기존 CLI 구독 활용, 추가 API 비용 없음 |
| 설정 오버헤드 | API 키, SDK, 환경 설정 | **제로 설정** — 이미 설치된 CLI 도구 활용 |

## 설치

```bash
pip install aqm
```

> Python 3.11+ 필요. LLM CLI가 최소 하나 설치되어 있어야 합니다:

| 런타임 | 제공업체 | 설치 |
|---|---|---|
| `claude` | Anthropic | `npm i -g @anthropic-ai/claude-code && claude login` |
| `gemini` | Google | `npm i -g @google/gemini-cli` |
| `codex` | OpenAI | `npm i -g @openai/codex` |

API 키나 SDK 설정 불필요 — aqm은 CLI 도구를 서브프로세스로 실행합니다. 기존 CLI 구독으로 사용하며, 별도 API 과금이 없습니다.

## 빠른 시작

```bash
cd my-project
aqm init                              # 대화형 설정 마법사
aqm run "JWT 인증 추가"                 # 파이프라인 실행
aqm serve                              # 웹 대시보드 (localhost:8000)
```

## 실전 예제

### 예제 1: 코드 리뷰 파이프라인

모든 PR이 기획, 구현, 리뷰, 테스트를 자동으로 거칩니다.

```yaml
agents:
  - id: planner
    runtime: gemini
    system_prompt: "구현 단계로 분해: {{ input }}"
    handoffs: [{ to: developer }]

  - id: developer
    runtime: claude
    mcp: [{ server: github }]
    system_prompt: "계획을 구현: {{ input }}"
    handoffs: [{ to: reviewer }]

  - id: reviewer
    runtime: gemini                    # 다른 LLM = 다른 관점
    system_prompt: "버그와 보안 이슈 리뷰: {{ input }}"
    gate:
      type: llm
      prompt: "프로덕션 준비 완료? OWASP Top 10 체크."
      max_retries: 3
    handoffs:
      - { to: qa, condition: on_approve }
      - { to: developer, condition: on_reject }

  - id: qa
    runtime: claude
    context_strategy: last_only        # 리뷰어 출력만 필요 → 55% 토큰 절감
    system_prompt: "테스트 작성: {{ input }}"
```

```bash
aqm run "데이터베이스, API, 프론트엔드를 포함한 사용자 설정 기능 추가"
```

### 예제 2: 아키텍처 결정 세션

여러 전문가가 동의할 때까지 토론 — 실제 설계 회의처럼.

```yaml
agents:
  - id: architect
    runtime: claude
    system_prompt: |
      소프트웨어 아키텍트입니다. 토론: {{ input }}
      이전 토론: {{ transcript }}

  - id: security
    runtime: gemini
    system_prompt: |
      보안 전문가입니다. 위협에 집중: {{ input }}
      이전 토론: {{ transcript }}

  - id: design_session
    type: session
    participants: [architect, security]
    max_rounds: 5
    consensus:
      method: vote
      keyword: "VOTE: AGREE"
      require: all
    summary_agent: architect
    handoffs: [{ to: developer }]
```

```
── 라운드 1 ──
  [architect] 상태 비저장 확장을 위한 JWT. 15분마다 토큰 갱신...
  [security] 토큰 폐기가 약점. 하이브리드 방식 고려...
── 라운드 2 ──
  [architect] 동의 — Redis 블랙리스트로 하이브리드. VOTE: AGREE  ✓
  [security] Redis 방식 적합. VOTE: AGREE  ✓
✓ 합의 도달 (라운드 2)
```

### 예제 3: 사람 승인이 필요한 배포

AI가 작업하고, 사람이 핵심 단계를 승인합니다.

```yaml
agents:
  - id: developer
    runtime: claude
    human_input:
      mode: before
      prompt: "어떤 기능이 필요한가요? 제약 조건은?"
    system_prompt: "구축: {{ input }}"
    handoffs: [{ to: deployer }]

  - id: deployer
    runtime: claude
    gate: { type: human }              # 수동 승인 전까지 파이프라인 일시정지
    system_prompt: "배포: {{ input }}"
```

```bash
aqm run "인증 모듈 리팩토링"
# → developer가 먼저 사용자 입력 요청
# → 코딩 후 deployer에서 파이프라인 일시정지
aqm approve T-ABC123 -r "LGTM, 스테이징에 배포"
```

## 주요 기능

### 멀티-LLM 런타임

에이전트별로 다른 LLM 혼합 사용. Claude가 코드 작성, Gemini가 리뷰, Codex가 테스트.

```yaml
agents:
  - id: planner
    runtime: gemini
    model: gemini-2.5-flash
    system_prompt: "계획: {{ input }}"
    handoffs: [{ to: developer }]

  - id: developer
    runtime: claude
    mcp: [{ server: github }]         # 자동 Code 모드
    system_prompt: "구현: {{ input }}"
```

### 대화형 세션

세션 노드는 여러 에이전트가 회의처럼 **라운드별로 토론**하여 합의에 도달합니다.

```yaml
agents:
  - id: design_review
    type: session
    participants: [architect, frontend, security]
    turn_order: round_robin           # 또는: moderator
    max_rounds: 5
    consensus:
      method: vote                    # 또는: moderator_decides
      keyword: "VOTE: AGREE"
      require: all                    # 또는: majority
    summary_agent: architect
    handoffs: [{ to: implementer }]
```

**합의 방식:**

| 방식 | 동작 |
|---|---|
| `vote` | 각 에이전트가 출력에 키워드를 포함. `all` 또는 `majority`가 동의하면 합의. |
| `moderator_decides` | `summary_agent`만 합의를 선언할 수 있음. |

`transcript.md` 회의록을 생성합니다. 자유롭게 혼합 가능: `배치 → 세션 → 배치`.

### 청크 분해

태스크를 추적 가능한 작업 단위로 분리. 에이전트가 출력 지시어로 청크를 관리합니다.

```yaml
- id: build_session
  type: session
  participants: [pm, dev]
  consensus:
    require_chunks_done: true         # 모든 청크가 완료되어야 합의
  chunks:
    enabled: true
    initial:
      - "프로젝트 구조 설정"
      - "인증 흐름 구현"
      - "유닛 테스트 추가"
```

**에이전트 지시어:**
```
CHUNK_ADD: 드래그앤드롭 구현       → 새 청크 추가
CHUNK_DONE: C-001                  → 청크 완료 표시
CHUNK_REMOVE: C-003                → 청크 삭제
```

템플릿 변수 `{{ chunks }}`로 프롬프트에 상태 테이블 주입. `chunks.json`에 저장.

**CLI:**
```bash
aqm chunks list T-ABC123
aqm chunks add T-ABC123 "새 기능"
aqm chunks done T-ABC123 C-001
aqm chunks remove T-ABC123 C-002
```

**웹 API:** `/api/tasks/{id}/chunks`에서 CRUD + SSE `chunk_update` 이벤트.

### 컨텍스트 전략 (토큰 최적화)

각 에이전트의 `context_strategy`가 `{{ context }}`에 포함되는 내용을 제어합니다. 불필요한 컨텍스트 주입을 방지하여 토큰을 절감합니다.

```yaml
agents:
  - id: planner
    context_strategy: both            # 전체 가시성 (기본값)

  - id: developer
    context_strategy: last_only       # 직전 stage만 → 55% 절감
    context_window: 1

  - id: deployer
    context_strategy: none            # 컨텍스트 없음 → 85% 절감
```

| 전략 | `{{ context }}` 내용 | 토큰 절감 | 용도 |
|---|---|---|---|
| `both` (기본값) | 공유 context.md + 에이전트 개인 노트 | — | 전체 가시성, 하위 호환 |
| `shared` | 스마트 윈도잉된 공유 context.md | ~동일 | 파이프라인 이력 필요 에이전트 |
| `last_only` | 직전 stage 출력만 | **~55%** | 이전 단계 결과만 필요한 에이전트 |
| `own` | 에이전트 개인 `agent_{id}.md`만 | **~85%** | 자기 노트만 참조하는 집중형 에이전트 |
| `none` | 비어있음 (컨텍스트 미주입) | **~85%** | 독립적인 에이전트 |

10-agent 파이프라인 벤치마크 (`tests/bench_token_efficiency.py`):
```
전략          총 토큰      절감률
both          12,233        0%
last_only      5,504       55%
none           1,873       85%
```

### 핸드오프 라우팅

태스크 흐름을 위한 세 가지 전략:

```yaml
# 고정 — 지정된 타겟
handoffs:
  - to: reviewer
    condition: always

# 팬아웃 — 여러 타겟에 동시 전달
handoffs:
  - to: qa, docs, deploy
    condition: on_approve

# 에이전트 결정 — 에이전트가 실행 시 타겟 선택
handoffs:
  - to: "*"
    condition: auto    # 에이전트가 출력에 HANDOFF: <id>를 포함
```

**조건:** `always`, `on_approve`, `on_reject`, `on_pass`, `auto`, 또는 표현식 (`severity == critical`)

**페이로드 변수:** `{{ output }}`, `{{ input }}`, `{{ reject_reason }}`, `{{ gate_result }}`

### 사람 입력 (Human-in-the-Loop)

```yaml
agents:
  - id: planner
    human_input:
      mode: before           # 에이전트 실행 전에 질문
      prompt: "어떤 기능이 필요한가요?"

  - id: developer
    human_input: true        # 약식: 에이전트가 HUMAN_INPUT: <질문>으로 중간에 요청 가능
```

**모드:**

| 모드 | 동작 |
|---|---|
| `before` | 에이전트 실행 전에 항상 일시정지하고 사용자에게 질문. |
| `on_demand` | 에이전트가 출력에 `HUMAN_INPUT: <질문>` 지시어로 요청. |
| `both` | 두 모드 모두 사용. |

### 게이트 (품질 관리)

```yaml
gate:
  type: llm              # LLM이 자동 평가 → 승인/반려
  prompt: "프로덕션 준비가 되었나요?"
  max_retries: 3         # 반려 → 최대 3번 재시도, 이후 실패

gate:
  type: human            # 파이프라인 일시정지 → aqm approve/reject
```

### 태스크 재시작 & 복구

실패하거나 완료된 태스크를 원하는 stage부터 재시작 — 처음부터 다시 할 필요 없음.

**동작 방식:**
- 각 stage 실행 전, aqm이 모든 컨텍스트 파일을 스냅샷 (context.md, 에이전트 노트, 트랜스크립트)
- 실패 시 런타임의 부분 출력도 보존
- `aqm restart`로 선택한 stage의 스냅샷에서 컨텍스트를 복원하고 재실행

```bash
# 실패 지점부터 재시작 (자동 감지)
aqm restart T-A3F2B1

# 특정 stage부터 재시작
aqm restart T-A3F2B1 --from-stage 3

# 처음부터 전체 재실행
aqm restart T-A3F2B1 --from-stage 1
```

`failed`, `completed`, `stalled`, `cancelled` 상태의 태스크에서 사용 가능. 웹 대시보드에서도 stage 선택 재시작 버튼 제공.

| 이벤트 | 동작 |
|--------|------|
| 각 stage 실행 전 | 컨텍스트 파일을 `snapshots/stage_N/`에 스냅샷 |
| 태스크 성공 완료 | 모든 스냅샷 정리 |
| 태스크 실패 | 재시작을 위해 스냅샷 보존 |
| `aqm restart --from-stage N` | 스냅샷에서 컨텍스트 복원, stage 잘라내기, 파이프라인 재개 |

### MCP 서버

[Model Context Protocol](https://modelcontextprotocol.io/)을 통해 에이전트에 실제 환경 연동 기능 부여.

```yaml
mcp:
  - server: github
  - server: filesystem
    args: ["/path/to/dir"]
  - server: custom-db
    command: node
    args: ["./mcp-server.js"]
    env: { DATABASE_URL: "postgres://..." }
```

### 파라미터 (이식 가능한 파이프라인)

```yaml
params:
  model: claude-sonnet-4-20250514
  project_path:
    type: string
    required: true
    prompt: "프로젝트 루트 경로?"

agents:
  - id: dev
    model: ${{ params.model }}
```

**재정의:** `aqm run "태스크" --param model=claude-opus-4-6`

### 임포트 / 상속

```yaml
imports:
  - from: ./shared/reviewers.yaml
    agents: [security_reviewer]

agents:
  - id: base_reviewer
    abstract: true
    runtime: claude
    gate: { type: llm }

  - id: code_reviewer
    extends: base_reviewer
    system_prompt: "코드 리뷰: {{ input }}"
```

### 파이프라인 레지스트리 (공유 & 탐색)

```bash
aqm search "code review"              # 커뮤니티 파이프라인 검색
aqm pull security-audit               # 한 명령으로 설치
aqm publish --name my-pipeline        # 내 파이프라인 공유
```

## CLI 레퍼런스

```bash
# 설정
aqm init                              # 대화형 설정 마법사
aqm validate                          # agents.yaml 검증
aqm agents                            # 에이전트 그래프 표시

# 실행
aqm run "JWT 인증 추가"                # 기본 파이프라인 실행
aqm run "버그 수정" --agent bug_fixer  # 특정 에이전트에서 시작
aqm run "API 구축" --pipeline backend  # 지정된 파이프라인
aqm run "태스크" --param model=opus    # 파라미터 재정의

# 관리
aqm list                              # 모든 태스크 목록
aqm status T-ABC123                   # 태스크 상세
aqm cancel T-ABC123                   # 태스크 취소
aqm fix T-ABC123 "색상 수정"           # 컨텍스트 포함 후속 작업
aqm restart T-ABC123                  # 실패 지점부터 재시작
aqm restart T-ABC123 --from-stage 2   # 특정 stage부터 재시작

# 게이트 & 사람 입력
aqm approve T-ABC123                  # 게이트 승인
aqm reject T-ABC123 -r "테스트 필요"   # 게이트 반려
aqm human-input T-ABC123 "응답"       # 에이전트 질문에 응답

# 청크
aqm chunks list T-ABC123              # 상태 테이블
aqm chunks done T-ABC123 C-001        # 완료 표시

# 파이프라인
aqm pipeline list                     # 파이프라인 목록
aqm pipeline create review --ai       # AI로 생성
aqm pipeline default review           # 기본 설정

# 레지스트리
aqm search "code review"              # 검색
aqm pull code-review-pipeline         # 설치
aqm publish --name my-pipeline        # 공유

# 대시보드
aqm serve                             # 웹 UI (localhost:8000)
```

## agents.yaml 레퍼런스

### 진입점 (자동 라우팅)

```yaml
entry_point: auto    # LLM이 사용자 입력에 따라 최적 에이전트 선택
# entry_point: first  # (기본값) 첫 번째 에이전트가 태스크 수신
```

### 에이전트 정의

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `id` | `string` | — | 고유 식별자 (필수) |
| `name` | `string` | `""` | 표시 이름 (비어있으면 id에서 자동 생성) |
| `type` | `"agent"` \| `"session"` | `"agent"` | 노드 타입 |
| `runtime` | `"claude"` \| `"gemini"` \| `"codex"` | — | `type: agent`일 때 필수 |
| `model` | `string` | CLI 기본값 | 모델 재정의 |
| `system_prompt` | `string` | `""` | Jinja2 템플릿: `{{ input }}`, `{{ context }}`, `{{ transcript }}`, `{{ chunks }}` |
| `context_strategy` | `"none"` \| `"last_only"` \| `"own"` \| `"shared"` \| `"both"` | `"both"` | 주입할 컨텍스트 (토큰 최적화) |
| `context_window` | `int` | `3` | 최근 N개 stage 전문 주입 (0 = 전체) |
| `human_input` | `boolean` \| `object` | `null` | 사람 입력 (`before`, `on_demand`, `both`) |
| `handoffs` | `list[Handoff]` | `[]` | 라우팅 규칙 |
| `gate` | `object` | `null` | 품질 게이트 |
| `mcp` | `list[MCPServer]` | `[]` | MCP 서버 연결 |
| `cli_flags` | `list[string]` | `null` | 런타임 추가 CLI 플래그 |
| `abstract` | `boolean` | `false` | 템플릿 전용 에이전트 (실행되지 않음) |
| `extends` | `string` | `null` | 부모 에이전트 ID (상속) |

### 핸드오프 필드

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `to` | `string` | — | 대상 에이전트 ID, 또는 팬아웃을 위한 쉼표 구분 (`"qa, docs"`) |
| `task` | `string` | `""` | 태스크 이름 라벨 |
| `condition` | `string` | `"always"` | `always`, `on_approve`, `on_reject`, `on_pass`, `auto`, 또는 표현식 |
| `payload` | `string` | `"{{ output }}"` | Jinja2 템플릿: `{{ output }}`, `{{ input }}`, `{{ reject_reason }}`, `{{ gate_result }}` |

### 세션 필드 (type: session)

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `participants` | `list[string]` | — | 에이전트 ID (필수) |
| `turn_order` | `"round_robin"` \| `"moderator"` | `"round_robin"` | 발언 순서 |
| `max_rounds` | `int` | `10` | 최대 라운드 수 |
| `consensus.method` | `"vote"` \| `"moderator_decides"` | `"vote"` | 합의 감지 방식 |
| `consensus.keyword` | `string` | `"VOTE: AGREE"` | 동의 신호 |
| `consensus.require` | `"all"` \| `"majority"` | `"all"` | 합의 기준 |
| `consensus.require_chunks_done` | `boolean` | `false` | 청크 완료 시 합의 |
| `summary_agent` | `string` | `null` | 최종 요약 담당 |
| `chunks.enabled` | `boolean` | `true` | 청크 추적 활성화 |
| `chunks.initial` | `list[string]` | `[]` | 초기 청크 목록 |

### config.yaml 레퍼런스

`.aqm/config.yaml`의 프로젝트 레벨 설정. 모든 필드는 선택사항.

```yaml
pipeline:
  max_stages: 20
gate:
  model: claude-sonnet-4-20250514
  timeout: 120
timeouts:
  claude: 600
  gemini: 600
  codex: 600
```

## 비교

| | LangGraph | CrewAI | AutoGen | aqm |
|---|---|---|---|---|
| 파이프라인 정의 | Python | Python + YAML | Python | **YAML만** |
| 파이프라인 공유 | ❌ | 유료 | ❌ | **오픈 레지스트리** |
| 멀티에이전트 토론 | ❌ | ❌ | 그룹챗 | **세션 노드 + 합의 투표** |
| 태스크 분해 | ❌ | ❌ | ❌ | **청크 추적** |
| 컨텍스트 최적화 | ❌ | 자동 요약 | ❌ | **5가지 전략 (55-85% 절감)** |
| 멀티 LLM | LangChain | LiteLLM | 다수 | **CLI 서브프로세스 (API 키 불필요)** |
| 비용 모델 | 토큰당 API | 토큰당 API | 토큰당 API | **CLI 구독 (추가 비용 없음)** |
| Human-in-the-Loop | 미들웨어 | 웹훅 | HumanProxy | **에이전트별 1등급 설정** |
| 품질 게이트 | ❌ | 콜백 | ❌ | **LLM + Human 게이트** |
| 자동 진입점 라우팅 | ❌ | ❌ | ❌ | **LLM 기반 `entry_point: auto`** |
| 팬아웃 병렬 | 수동 | 수동 | ❌ | **선언적** |
| 실시간 스트리밍 | ❌ | ❌ | ❌ | **토큰 단위 SSE** |
| 웹 대시보드 | 유료 | 유료 | ❌ | **내장 (무료)** |

## 아키텍처

```
aqm/
├── core/
│   ├── agent.py          # AgentDefinition, ConsensusConfig, ChunksConfig, HumanInputConfig
│   ├── pipeline.py       # 파이프라인 루프 + _run_session() + 컨텍스트 전략
│   ├── chunks.py         # Chunk 모델, ChunkManager, 지시어 파서
│   ├── task.py           # Task, StageRecord, TaskStatus
│   ├── gate.py           # LLMGate / HumanGate
│   ├── context_file.py   # context.md + agent_{id}.md + transcript.md + 스마트 윈도잉
│   ├── context.py        # Jinja2 프롬프트 빌더
│   ├── config.py         # ProjectConfig (.aqm/config.yaml)
│   └── project.py        # 프로젝트 루트 감지
├── queue/
│   ├── base.py           # AbstractQueue 인터페이스
│   ├── sqlite.py         # SQLiteQueue (프로덕션)
│   └── file.py           # FileQueue (테스트용)
├── runtime/
│   ├── base.py           # AbstractRuntime 인터페이스
│   ├── claude_code.py    # Claude Code (MCP 포함, 토큰 스트리밍)
│   ├── gemini.py         # Gemini CLI
│   └── codex.py          # Codex CLI
├── web/
│   ├── app.py            # FastAPI 앱 팩토리
│   ├── templates.py      # 공유 CSS/레이아웃/헬퍼
│   ├── pages/            # 페이지 렌더러 (dashboard, agents, registry, validate, task_detail)
│   └── api/              # REST + 청크 + SSE + 사람 입력 엔드포인트
├── registry.py           # GitHub 파이프라인 레지스트리
└── cli.py                # Click CLI
```

## 커뮤니티

**[Discord](https://discord.gg/798f3rED)** | **[레지스트리](https://github.com/aqm-framework/registry)** | **[JSON 스키마](../schema/agents-schema.json)**

## 기여

```bash
git clone https://github.com/aqm-framework/aqm
cd aqm
pip install -e ".[dev,serve]"
pytest tests/
```

파이프라인 기여는 코드 기여와 동등하게 가치를 둡니다. [CONTRIBUTING.md](../CONTRIBUTING.md)를 참조하세요.

## 라이선스

MIT
