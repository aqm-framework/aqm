# aqm Registry — Seed Pipelines

Ten ready-to-use agent pipelines that showcase the key differentiators of **agent-queue (aqm)**. Each pipeline is a complete `agents.yaml` you can customize via `params:` and run immediately.

---

## Pipelines

### 1. Software Feature Development
**`software-feature-pipeline/agents.yaml`**

End-to-end feature development: plan a spec, review it (LLM gate), implement in code, and QA (human gate). Rejected specs loop back to the planner; failed QA loops back to the developer.

| Feature | Details |
|---|---|
| LLM gate | Spec review with configurable strictness |
| Human gate | QA sign-off before merge |
| Reverse feedback loop | Reject -> planner / developer |
| MCP servers | `github`, `filesystem` |
| Params | `project_language`, `test_framework`, `review_strictness`, `target_branch` |

```bash
agent-queue run examples/software-feature-pipeline --input "Add OAuth2 login"
```

---

### 2. Content Creation
**`content-pipeline/agents.yaml`**

Research, write, edit, and publish content. The editor runs an LLM gate; rejected drafts return to the writer with specific feedback.

| Feature | Details |
|---|---|
| LLM gate | Editorial quality check |
| Reverse feedback loop | Reject -> writer |
| Params | `tone`, `audience`, `word_count`, `content_type`, `publish_platform` |

```bash
agent-queue run examples/content-pipeline --input "Guide to microservices"
```

---

### 3. PR Code Review (Fan-Out)
**`code-review-pipeline/agents.yaml`**

Loads a PR diff and fans out to three parallel reviewers — security, performance, and style — then merges results into a unified review summary.

| Feature | Details |
|---|---|
| Fan-out | `to: "security, performance, style"` |
| MCP servers | `github` |
| Context accumulation | Each reviewer's findings merge in `context.md` |
| Params | `repo`, `language`, `security_focus`, `performance_threshold`, `style_guide` |

```bash
agent-queue run examples/code-review-pipeline --input "https://github.com/org/repo/pull/42"
```

---

### 4. Customer Support Triage
**`customer-support-pipeline/agents.yaml`**

Classifies tickets, then an auto-router agent decides which specialist handles it (billing, technical, or account) by emitting `HANDOFF: agent_id`. Escalation uses a human gate.

| Feature | Details |
|---|---|
| Agent-decided routing | `condition: auto` with `HANDOFF:` directives |
| Human gate | Escalation manager approval |
| Reverse feedback loop | Rejected responses -> re-routed |
| Params | `company_name`, `support_tone`, `sla_response_minutes`, `escalation_threshold` |

```bash
agent-queue run examples/customer-support-pipeline --input "I was charged twice"
```

---

### 5. Data Analysis
**`data-analysis-pipeline/agents.yaml`**

Collect data from a database, clean it, analyze for patterns, and generate an executive report. LLM gate ensures report quality; rejection loops back to the analyst.

| Feature | Details |
|---|---|
| LLM gate | Report quality check |
| Reverse feedback loop | Reject -> analyst |
| MCP servers | `postgres`, `filesystem` |
| Params | `database`, `output_format`, `date_range`, `confidence_level`, `report_audience` |

```bash
agent-queue run examples/data-analysis-pipeline --input "Q4 sales trends by region"
```

---

### 6. Incident Response
**`incident-response-pipeline/agents.yaml`**

Detect and triage an incident, auto-route by severity (SEV1-2 to critical responder, SEV3-4 to standard), verify the fix (LLM gate), and generate a blameless postmortem.

| Feature | Details |
|---|---|
| Agent-decided routing | `condition: auto` routes by severity |
| LLM gate | Fix verification |
| Reverse feedback loop | Verification failure -> back to fix |
| MCP servers | `github`, `filesystem` |
| Params | `service_name`, `environment`, `on_call_team`, `runbook_path`, `notification_channel` |

```bash
agent-queue run examples/incident-response-pipeline --input "API p99 latency spike to 5s"
```

---

### 7. New Employee Onboarding
**`onboarding-pipeline/agents.yaml`**

Provision accounts, assign resources, then fan-out IT and HR setup in parallel. A manager reviews the complete onboarding package via human gate.

| Feature | Details |
|---|---|
| Fan-out | `to: "it_setup, hr_setup"` |
| Human gate | Manager sign-off |
| Reverse feedback loop | Rejected onboarding -> revise resources |
| Params | `company_name`, `department`, `default_tools`, `onboarding_buddy_program`, `probation_days` |

```bash
agent-queue run examples/onboarding-pipeline --input "New hire: Jane Smith, Senior Engineer, starts 2026-04-01"
```

---

### 8. Legal Document Review
**`legal-review-pipeline/agents.yaml`**

Extract key terms, summarize in plain English, flag risks (LLM gate), and route to human legal counsel for final approval. High-risk contracts loop back for deeper analysis.

| Feature | Details |
|---|---|
| LLM gate | Risk assessment threshold |
| Human gate | Legal counsel final approval |
| Reverse feedback loop | High risk -> re-extract; counsel rejection -> reassess |
| MCP servers | `filesystem` |
| Params | `jurisdiction`, `company_name`, `contract_type`, `risk_tolerance`, `legal_team_email` |

```bash
agent-queue run examples/legal-review-pipeline --input "Review /contracts/vendor-2026-q1.pdf"
```

---

### 9. Release Management
**`release-pipeline/agents.yaml`**

Generate a changelog from git history, bump versions, run the test suite (LLM gate on coverage), and request human deploy approval. Test failures loop back to fix.

| Feature | Details |
|---|---|
| LLM gate | Test pass/coverage threshold |
| Human gate | Deploy approval |
| Reverse feedback loop | Test failure -> version bump fix |
| MCP servers | `github`, `filesystem` |
| Params | `repo`, `version_strategy`, `release_branch`, `deploy_target`, `required_test_coverage` |

```bash
agent-queue run examples/release-pipeline --input "Prepare release v2.4.0"
```

---

### 10. Blog SEO Optimization
**`blog-seo-pipeline/agents.yaml`**

Analyze a blog post for SEO weaknesses, rewrite with optimizations, fan-out readability and technical SEO checks in parallel, gate on target score, and publish.

| Feature | Details |
|---|---|
| Fan-out | `to: "readability_checker, technical_checker"` |
| LLM gate | SEO score threshold |
| Reverse feedback loop | Below target score -> rewriter |
| MCP servers | `filesystem` |
| Params | `target_keyword`, `secondary_keywords`, `target_word_count`, `reading_level`, `target_seo_score` |

```bash
agent-queue run examples/blog-seo-pipeline --input "/blog/posts/kubernetes-tutorial.md"
```

---

## Feature Coverage Matrix

| Pipeline | LLM Gate | Human Gate | Reverse Loop | Fan-Out | Auto Routing | MCP Servers | Params |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Software Feature | x | x | x | | | x | x |
| Content Creation | x | | x | | | | x |
| Code Review | | | | x | | x | x |
| Customer Support | | x | x | | x | | x |
| Data Analysis | x | | x | | | x | x |
| Incident Response | x | | x | | x | x | x |
| Onboarding | | x | x | x | | | x |
| Legal Review | x | x | x | | | x | x |
| Release Management | x | x | x | | | x | x |
| Blog SEO | x | | x | x | | x | x |

---

## Priority Top 3

These three pipelines should be built and polished first for maximum launch impact:

### 1. PR Code Review (`code-review-pipeline`)
**Why first:** Every development team reviews PRs. This pipeline shows aqm's most visually distinctive feature -- fan-out parallel branches -- in a workflow that developers already do daily. It is easy to demo (just point at a real PR URL), requires no infrastructure beyond a GitHub token, and the three-reviewer-to-summary flow is immediately understandable. It sells the "declarative orchestration" story better than any other example.

### 2. Software Feature Development (`software-feature-pipeline`)
**Why second:** This is the flagship end-to-end pipeline. It combines the most differentiators in one flow (LLM gate, human gate, reverse feedback loops, MCP, params) and maps to the core use case that AI-native teams care about: shipping features faster. It demonstrates that aqm is not just a toy -- it can orchestrate real code-writing agents through a full SDLC cycle with quality gates.

### 3. Customer Support Triage (`customer-support-pipeline`)
**Why third:** This pipeline targets a completely different audience (ops/support teams), broadening aqm's appeal beyond pure engineering. The auto-routing feature (`condition: auto` with `HANDOFF:`) is unique to aqm and hard to replicate in other frameworks. It shows that agent-decided routing works in practice and opens the door to any classify-then-route use case (helpdesk, sales lead routing, document classification).
