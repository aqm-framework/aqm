<sub>📖 [English](../README.md)</sub>

# aqm

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

### aqm은 aqm으로 개발됩니다

aqm 자체 개발에 aqm을 사용합니다. 기능 요청이 들어오면 영향도 분석 → 구현 → 실제 YAML 파일로 테스트 → 문서 업데이트 → 코드 리뷰까지 자동으로 진행됩니다.

```
[사용자 요청]
     │
     ▼
[impact_analyzer] ── 영향 파일, 브레이킹 체인지 분석
     │
     ▼
[implementer] ── feature 브랜치 생성, 코드 작성 (MCP: github)
     │
     ▼
[tester] ── pytest + /tmp/에 실제 YAML 생성 + aqm validate
     │ gate: llm
     ├─ on_approve ──► [doc_updater] ── git diff → 변경된 섹션만 업데이트
     └─ on_reject  ──► [implementer] ── 수정 후 재시도
                            │
                       [code_reviewer] ── 머지 전 품질 게이트 (runtime: gemini)
                            │ gate: llm
                            ├─ on_approve ──► [branch_manager] ── 커밋 + main 머지
                            └─ on_reject  ──► [fixer] ──► [code_reviewer]
```

```yaml
# .aqm/pipelines/dev.yaml (요약)
agents:
  - id: impact_analyzer
    runtime: claude
    context_strategy: none             # 분석 단계는 이전 컨텍스트 불필요
    system_prompt: "영향도 분석: {{ input }}"
    handoffs: [{ to: implementer }]

  - id: implementer
    runtime: claude
    context_strategy: last_only        # 영향도 보고서만 필요
    mcp: [{ server: github }]
    system_prompt: "feature 브랜치에서 구현. 커밋하지 말 것."
    handoffs: [{ to: tester }]

  - id: tester
    runtime: claude
    context_strategy: last_only
    system_prompt: |
      pytest 실행. /tmp/aqm_test_<feature>/에 실제 YAML 생성.
      aqm validate 실행. pytest exit code + PASS | FAIL 리포트.
    gate:
      type: llm
      prompt: |
        Status=PASS, exit code=0, 회귀 실패 0건일 때만 APPROVE.
        부분 통과(예: 3통과 1실패)도 FAIL로 처리.
      max_retries: 2
    handoffs:
      - { to: doc_updater, condition: on_approve }
      - { to: implementer, condition: on_reject }

  - id: doc_updater
    runtime: claude
    context_strategy: last_only
    system_prompt: "git diff main 실행. 변경된 문서 섹션만 업데이트."
    handoffs: [{ to: code_reviewer }]

  - id: code_reviewer
    runtime: gemini                    # 교차 LLM: 머지 전 다른 시각으로 검토
    context_strategy: last_only
    system_prompt: "git diff main과 pytest 실행. 머지 전 검토."
    gate:
      type: llm
      max_retries: 3
    handoffs:
      - { to: branch_manager, condition: on_approve }
      - { to: fixer, condition: on_reject }

  - id: branch_manager
    runtime: claude
    context_strategy: last_only
    mcp: [{ server: github }]
    system_prompt: "리뷰 승인 완료. 변경사항 커밋, feature 브랜치를 main에 머지."

  - id: fixer
    runtime: claude
    context_strategy: both             # 거부 이유 + 코드 맥락 모두 필요
    mcp: [{ server: github }]
    system_prompt: "feature 브랜치에서 리뷰 문제 수정. pytest 재실행."
    handoffs: [{ to: code_reviewer }]
```

```bash
cd aqm
aqm run "aqm validate에 --strict 플래그 추가" --pipeline dev
# → 7개 에이전트, 완전 자동화: 분석 → 구현 → 테스트 → 문서 → 리뷰 → 머지
```

이 방식으로 추가된 기능: `aqm validate --strict`, 리소스 가용성 검사, 재시도 전략 — 테스트나 문서를 직접 작성하지 않고 모두 자동으로 완성.

**실측 파이프라인 실행 시간** (실제 실행 기준, code_reviewer만 Gemini, 나머지 Claude):

| 단계 | T-4D9511 정상 실행 | T-8A833D 재시작 포함 |
|---|---|---|
| impact_analyzer | — ¹ | — ¹ |
| implementer | 12분 | 6분 |
| tester | 4분 | 5분 |
| doc_updater | 1.5분 | 13.5분 ² |
| branch_manager | 1분 | 1분 |
| code_reviewer | 5분 | 1분 |
| **전체 (2–6단계 합계)** | **23.5분** | **25.8분** |

¹ impact_analyzer 시작 시간 미기록 (약 5분 추가 소요)
² doc_updater 1회 실패 후 `aqm restart --from-stage 4`로 복구

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
aqm validate --strict                 # 경고를 오류로 처리 (exit 1)
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

### 전체 예시

```yaml
agents:
  - id: developer
    runtime: claude
    system_prompt: "구현: {{ input }}"
    handoffs:
      - to: reviewer
        condition: always
      - to: "qa, docs"               # 쉼표 = 팬아웃 (동시 전달)
        condition: on_approve
    gate:
      type: llm                       # llm (자동) | human (수동 승인)
      prompt: "프로덕션 준비?"
      max_retries: 3
    mcp:
      - server: github                # 단축형 — 자동 npx 패키지
      - server: filesystem
        args: ["/path"]
      - server: custom
        command: node
        args: ["./server.js"]
        env: { KEY: "value" }
    context_strategy: last_only       # both | shared | last_only | own | none
    human_input:
      mode: before                    # before | on_demand | both
      prompt: "어떤 기능이 필요한가요?"
    cli_flags: ["--verbose"]          # 런타임에 전달할 추가 플래그
    retry:
      max_retries: 2                  # 런타임 오류 시 최대 2회 재시도
      backoff: 5                      # 재시도 간 5초 대기
      fallback_context_strategy: last_only  # 재시도 시 컨텍스트 축소

  - id: design_session
    type: session
    participants: [architect, security]
    turn_order: round_robin           # round_robin | moderator
    max_rounds: 5
    consensus:
      method: vote                    # vote | moderator_decides
      keyword: "VOTE: AGREE"
      require: all                    # all | majority
    summary_agent: architect
    chunks:
      enabled: true
      initial: ["프로젝트 설정", "인증 구현"]
```

### 주요 필드 요약

| 필드 | 작성 예시 |
|---|---|
| `handoffs` | `[{ to: reviewer }, { to: "qa, docs", condition: on_approve }]` |
| `gate` | `{ type: llm, prompt: "OK?", max_retries: 3 }` |
| `mcp` | `[{ server: github }, { server: fs, args: ["/dir"] }]` |
| `human_input` | `true` 또는 `{ mode: before, prompt: "질문?" }` |
| `retry` | `{ max_retries: 2, backoff: 5, fallback_context_strategy: last_only }` |
| `participants` | `[agent_a, agent_b, agent_c]` |
| `chunks.initial` | `["설정", "구현", "테스트"]` |

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
