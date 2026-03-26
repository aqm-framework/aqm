# aqm

여러 AI 에이전트가 **명시적 큐**를 통해 태스크를 전달하거나, **실시간 세션**에서 합의에 도달할 때까지 토론하는 오케스트레이션 프레임워크입니다.

YAML로 파이프라인을 정의하고, 누구와든 공유하고, 로컬에서 실행하세요.

**[English Documentation](../README.md)**

```
  [사용자] ──입력──► [기획자] ──► [리뷰어] ──승인──► [설계 세션] ──► [구현자]
                        ▲              │               ┌──┬──┬──┐
                        └── 반려 ──────┘               ▼  ▼  ▼  ▼  라운드 로빈
                        └── 질문 ──►[사용자]          [아키][보안][FE]  합의까지
```

## 설치

```bash
pip install aqm
```

> Python 3.11+ 필요. LLM CLI가 최소 하나 설치되어 있어야 합니다 (아래 멀티-LLM 참조).

## 빠른 시작

```bash
cd my-project
aqm init                              # 대화형 설정 마법사
aqm run "JWT 인증 추가"                 # 파이프라인 실행
aqm serve                              # 웹 대시보드 (localhost:8000)
```

## 주요 기능

### 멀티-LLM 런타임

에이전트별로 다른 LLM 제공업체를 혼합 사용. 모두 CLI 서브프로세스로 실행 — API 키나 SDK 설정 불필요.

| 런타임 | 제공업체 | 설치 |
|---|---|---|
| `claude` | Anthropic | `npm i -g @anthropic-ai/claude-code && claude login` |
| `gemini` | Google | `npm i -g @google/gemini-cli` |
| `codex` | OpenAI | `npm i -g @openai/codex` |

Claude는 항상 **Code 모드**로 실행 — MCP 서버와 도구는 설정 시 사용 가능합니다.

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

세션 노드는 여러 에이전트가 회의처럼 **라운드별로 토론**하여 합의에 도달할 수 있게 합니다.

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

**CLI 출력:**
```
── 라운드 1 ──
  [architect] JWT가 상태 비저장 확장에 유리합니다...
  [security] 토큰 폐기 문제가 있습니다...
── 라운드 2 ──
  [architect] 하이브리드 방식. VOTE: AGREE  ✓
  [security] VOTE: AGREE  ✓
✓ 합의 도달 (라운드 2)
```

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

각 에이전트의 `context_strategy`가 `{{ context }}`에 포함되는 내용을 제어합니다. `context_window`로 최근 N개 stage만 전문 주입하고 나머지는 요약합니다.

```yaml
agents:
  - id: planner
    runtime: claude
    context_strategy: both            # 전체 가시성 (기본값)
    system_prompt: "계획: {{ input }} 컨텍스트: {{ context }}"

  - id: developer
    runtime: claude
    context_strategy: last_only       # 직전 stage만 → 55% 절감
    context_window: 1
    system_prompt: "구현: {{ input }} 이전: {{ context }}"

  - id: qa
    runtime: claude
    context_strategy: shared          # 스마트 윈도잉 이력
    context_window: 2
    system_prompt: "테스트: {{ input }} 이력: {{ context }}"

  - id: deployer
    runtime: claude
    context_strategy: none            # 컨텍스트 없음 → 85% 절감
    system_prompt: "배포: {{ input }}"
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

**스마트 윈도잉 (`context_window`):**
- `context_window: 3` (기본값) — 오래된 stage는 한 줄 요약, 최근 3개만 전문 주입
- `context_window: 0` — 전체 주입 (이전 동작과 동일)
- `context_window: 1` — 최대 압축, 15-stage 파이프라인에서 **72% 이상 절감**

**태스크별 파일 구조:**
```
.aqm/tasks/{task_id}/
├── context.md              # 공유 (모든 stage, 단일 원본)
├── agent_architect.md      # 아키텍트의 출력 기록 (출력만 저장)
├── agent_developer.md      # 개발자의 출력 기록
├── transcript.md           # 세션 회의록
├── chunks.json             # 청크 추적
└── current_payload.md      # 마지막 핸드오프 페이로드
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

에이전트가 파이프라인 실행 중 사람에게 입력을 요청할 수 있습니다 — 요구사항 확인, 피드백 수집, 사람의 판단이 필요한 결정을 위해.

```yaml
agents:
  - id: planner
    runtime: claude
    human_input:
      enabled: true
      mode: before           # 에이전트 실행 전에 질문
      prompt: "어떤 기능이 필요한가요? 디자인 선호사항은?"
    system_prompt: |
      사용자의 요구사항을 기반으로 프로젝트를 계획하세요.
      {{ input }}

  - id: developer
    runtime: claude
    human_input: true        # on_demand 모드 약식 표현
    system_prompt: |
      계획을 구현하세요. 확인이 필요하면:
      HUMAN_INPUT: <질문 내용>
      {{ input }}
```

**모드:**

| 모드 | 동작 |
|---|---|
| `before` | 에이전트 실행 전에 항상 일시정지하고 사용자에게 질문. 요구사항 수집에 적합. |
| `on_demand` | 에이전트가 출력에 `HUMAN_INPUT: <질문>` 지시어로 요청. 실행 중 확인에 적합. |
| `both` | 두 모드 모두 사용. |

**약식 표현:**
```yaml
human_input: true              # { enabled: true, mode: on_demand }과 동일
human_input: "before"          # { enabled: true, mode: before }과 동일
human_input:
  enabled: true
  mode: before
  prompt: "맞춤 질문"          # 'before' 모드에서 사용자에게 표시
```

사람의 응답은 `context.md` (공유)와 `agent_{id}.md` (개인) 모두에 기록되어 모든 에이전트가 참조할 수 있습니다.

**웹 대시보드**에서는 에이전트가 입력을 요청할 때 시안색 입력 패널을 표시합니다.

### 게이트 (품질 관리)

```yaml
gate:
  type: llm              # LLM이 자동 평가 → 승인/반려
  prompt: "프로덕션 준비가 되었나요?"

gate:
  type: human            # 파이프라인 일시정지 → aqm approve/reject
```

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
    auto_detect: "Read package.json name"

agents:
  - id: dev
    model: ${{ params.model }}
```

**재정의:** `aqm run "태스크" --param model=claude-opus-4-6`

**우선순위:** CLI 플래그 > params.yaml > 대화형 입력 > 기본값

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

## CLI 레퍼런스

### 프로젝트 설정

```bash
aqm init                          # 대화형: [1] AI 생성 [2] 템플릿 [3] 레지스트리에서 pull
aqm init --path ./my-project      # 특정 디렉토리에 초기화
aqm validate                      # agents.yaml 스키마 검증
aqm validate --pipeline review    # 특정 파이프라인 검증
aqm agents                        # 에이전트 그래프 및 연결 표시
```

### 파이프라인 실행

```bash
aqm run "JWT 인증 추가"                           # 기본 파이프라인 실행
aqm run "로그인 버그 수정" --agent bug_fixer       # 특정 에이전트에서 시작
aqm run "API 구축" --pipeline backend              # 지정된 파이프라인 실행
aqm run "배포" --priority critical                 # 우선순위 설정 (critical|high|normal|low)
aqm run "테스트" --param model=claude-opus-4-6     # 파이프라인 파라미터 재정의
aqm run "태스크" --parallel                        # 다른 태스크와 병렬 실행
```

### 태스크 관리

```bash
aqm list                          # 모든 태스크 목록
aqm list --filter completed       # 상태별 필터 (pending|in_progress|completed|failed|cancelled)
aqm status T-ABC123               # 상세 태스크 상태 + stage 이력
aqm context T-ABC123              # 태스크의 전체 context.md 보기
aqm priority T-ABC123 high        # 우선순위 변경 (critical|high|normal|low)
aqm cancel T-ABC123               # 실행 중/대기 중 태스크 취소
aqm fix T-ABC123 "색상 수정해줘"    # 이전 태스크 컨텍스트를 포함한 후속 태스크
```

### 게이트 & 사람 입력

```bash
aqm approve T-ABC123              # 휴먼 게이트 승인 (파이프라인 재개)
aqm approve T-ABC123 -r "LGTM"   # 사유와 함께 승인
aqm reject T-ABC123 -r "테스트 필요"    # 휴먼 게이트 반려 (사유 필수)
aqm human-input T-ABC123 "PostgreSQL에 다크모드로 해주세요"   # 에이전트 질문에 응답
```

### 청크 (작업 단위)

```bash
aqm chunks list T-ABC123          # 청크 상태 테이블 표시
aqm chunks add T-ABC123 "에러 핸들링 추가"    # 새 청크 추가
aqm chunks done T-ABC123 C-001    # 청크 완료 표시
aqm chunks remove T-ABC123 C-002  # 청크 삭제
```

### 파이프라인 관리

```bash
aqm pipeline list                 # 모든 파이프라인 목록 (★ = 기본)
aqm pipeline create review        # 새 파이프라인 생성 (대화형)
aqm pipeline create review --ai   # AI로 파이프라인 생성
aqm pipeline create review --template   # 템플릿에서 생성
aqm pipeline edit review          # AI 도움으로 파이프라인 편집
aqm pipeline default review       # 기본 파이프라인 설정
aqm pipeline delete review        # 파이프라인 삭제
```

### 레지스트리 (공유 & 탐색)

```bash
aqm search                        # 사용 가능한 모든 파이프라인 목록
aqm search "code review"          # 키워드 검색
aqm search --offline              # 로컬 레지스트리만 검색
aqm pull code-review-pipeline     # 레지스트리에서 파이프라인 설치
aqm pull my-pipeline --repo org/registry   # 커스텀 레지스트리에서 pull
aqm publish --name my-pipeline    # GitHub 레지스트리에 게시 (PR 생성)
aqm publish --local               # 로컬 레지스트리에만 저장
```

### 웹 대시보드

```bash
aqm serve                         # localhost:8000에서 시작
aqm serve --port 3000             # 커스텀 포트
aqm serve --host 0.0.0.0          # 원격 접속 허용
```

## agents.yaml 레퍼런스

### 진입점 (자동 라우팅)

어떤 에이전트가 사용자의 입력을 처음 받을지 제어:

```yaml
entry_point: auto    # LLM이 사용자 입력에 따라 최적 에이전트 선택
# entry_point: first  # (기본값) YAML 목록의 첫 번째 에이전트가 태스크 수신
```

| 값 | 동작 |
|---|---|
| `first` (기본값) | YAML 목록의 첫 번째 에이전트가 태스크를 수신. 하위 호환. |
| `auto` | LLM이 사용자 입력을 모든 에이전트와 비교하여 가장 적절한 에이전트 선택. |

**예시 — 자동 라우팅 멀티 도메인 파이프라인:**
```yaml
entry_point: auto

agents:
  - id: code_reviewer
    runtime: claude
    system_prompt: "코드 리뷰: {{ input }}"
  - id: bug_fixer
    runtime: claude
    system_prompt: "버그 수정: {{ input }}"
  - id: feature_planner
    runtime: claude
    system_prompt: "기능 계획: {{ input }}"
```

```bash
aqm run "PR #42 리뷰"            # → code_reviewer 자동 선택
aqm run "로그인 크래시 수정"       # → bug_fixer 자동 선택
aqm run "다크 모드 추가"           # → feature_planner 자동 선택
aqm run "버그 수정" --agent planner  # → --agent 플래그가 자동 선택 무시
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
| `handoffs` | `list[Handoff]` | `[]` | 라우팅 규칙 (아래 참조) |
| `gate` | `object` | `null` | 품질 게이트 (아래 참조) |
| `mcp` | `list[MCPServer]` | `[]` | MCP 서버 연결 (아래 참조) |
| `claude_code_flags` | `list[string]` | `null` | Claude 추가 CLI 플래그 |
| `abstract` | `boolean` | `false` | 템플릿 전용 에이전트 (실행되지 않음) |
| `extends` | `string` | `null` | 부모 에이전트 ID (상속) |

### 핸드오프 필드

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `to` | `string` | — | 대상 에이전트 ID, 또는 팬아웃을 위한 쉼표 구분 (`"qa, docs"`) |
| `task` | `string` | `""` | 태스크 이름 라벨 |
| `condition` | `string` | `"always"` | `always`, `on_approve`, `on_reject`, `on_pass`, `auto`, 또는 표현식 |
| `payload` | `string` | `"{{ output }}"` | Jinja2 템플릿: `{{ output }}`, `{{ input }}`, `{{ reject_reason }}`, `{{ gate_result }}` |

### 게이트 필드

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `type` | `"llm"` \| `"human"` | `"llm"` | LLM 자동 평가 또는 사람 수동 승인 |
| `prompt` | `string` | `""` | 커스텀 평가 프롬프트 (Jinja2: `{{ output }}`, `{{ input }}`) |
| `model` | `string` | config 기본값 | LLM 게이트 평가용 모델 재정의 |
| `max_retries` | `int` | `3` | 게이트 반려 최대 재시도 횟수 (무한 루프 방지) |

### MCP 서버 필드

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `server` | `string` | — | 서버 이름 (예: `"github"`, `"filesystem"`) |
| `command` | `string` | `"npx"` | 커스텀 명령어 (기본: `npx -y @modelcontextprotocol/server-{name}`) |
| `args` | `list[string]` | `[]` | 명령어 인수 |
| `env` | `object` | `null` | 환경 변수 |

### 사람 입력 필드

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `enabled` | `boolean` | `true` | 사람 입력 활성화/비활성화 |
| `mode` | `"before"` \| `"on_demand"` \| `"both"` | `"on_demand"` | 입력 요청 시점 |
| `prompt` | `string` | `""` | 사용자에게 표시할 커스텀 질문 (`before` 모드용) |

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

`.aqm/config.yaml`의 프로젝트 레벨 설정. 모든 필드는 선택사항 — 기본값이 내장되어 있습니다.

```yaml
# .aqm/config.yaml
pipeline:
  max_stages: 20              # 최대 파이프라인 stage 수

gate:
  model: claude-sonnet-4-20250514   # LLM 게이트 평가 기본 모델
  timeout: 120                # 게이트 평가 타임아웃 (초)
  system_prompt: |            # 커스텀 게이트 평가 프롬프트
    당신은 품질 게이트 평가자입니다...

timeouts:                     # 런타임 서브프로세스 타임아웃 (초)
  claude: 600
  gemini: 600
  codex: 600
```

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `pipeline.max_stages` | `int` | `20` | 최대 파이프라인 stage 수 |
| `gate.model` | `string` | `"claude-sonnet-4-20250514"` | LLM 게이트 평가 기본 모델 |
| `gate.timeout` | `int` | `120` | 게이트 서브프로세스 타임아웃 (초) |
| `gate.system_prompt` | `string` | 내장 프롬프트 | 커스텀 게이트 시스템 프롬프트 |
| `timeouts.claude` | `int` | `600` | Claude 런타임 타임아웃 |
| `timeouts.gemini` | `int` | `600` | Gemini CLI 런타임 타임아웃 |
| `timeouts.codex` | `int` | `600` | Codex CLI 런타임 타임아웃 |

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
│   └── project.py        # 프로젝트 루트 감지
├── queue/
│   ├── sqlite.py         # SQLiteQueue (프로덕션)
│   └── file.py           # FileQueue (테스트용)
├── runtime/
│   ├── claude_code.py    # Claude Code (MCP 포함, 토큰 스트리밍)
│   ├── gemini.py         # Gemini CLI
│   └── codex.py          # Codex CLI
├── web/
│   ├── app.py            # FastAPI + SSE
│   └── api/              # REST + 청크 + SSE + 사람 입력 엔드포인트
├── registry.py           # GitHub 파이프라인 레지스트리
└── cli.py                # Click CLI
```

## 비교

| | LangGraph | CrewAI | OpenSWE | aqm |
|---|---|---|---|---|
| 파이프라인 정의 | Python | Python | 코드 | **YAML** |
| 파이프라인 공유 | ❌ | 유료 | ❌ | **오픈 레지스트리** |
| 멀티 에이전트 토론 | ❌ | ❌ | ❌ | **세션 노드** |
| 태스크 분해 | ❌ | ❌ | ❌ | **청크 추적** |
| 컨텍스트 최적화 | ❌ | ❌ | ❌ | **에이전트별 컨텍스트 전략** |
| 멀티 LLM | 수동 | 제한적 | ❌ | **Claude + Gemini + Codex** |
| Human-in-the-Loop | ❌ | ❌ | ❌ | **에이전트별 `human_input`** |
| 승인/반려 게이트 | Interrupt | ❌ | ❌ | **First-class** |
| 자동 진입점 라우팅 | ❌ | ❌ | ❌ | **LLM 기반 `entry_point: auto`** |
| 팬아웃 병렬 | 수동 | ❌ | ❌ | **선언적** |
| 파일 기반 컨텍스트 | ❌ | ❌ | ❌ | **context.md + 에이전트 파일** |
| 실시간 스트리밍 | ❌ | ❌ | ❌ | **토큰 단위 SSE 스트리밍** |
| 웹 대시보드 | ❌ | 유료 | ❌ | **내장** |

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
