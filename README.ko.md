# GJC OpenRouter Preset Sync

- **문제:** 모델 목록을 수동 편집하지 않고 GJC 모델 프리셋을 최신으로 유지합니다.
- **입력:** OpenRouter 사용량 데이터, 이미 등록한 모델, 기계 채점된 품질 증거.
- **출력:** 다섯 역할의 primary/fallback 프로필. 미리보기가 기본이고 요청할 때만 적용합니다.

언어: [English](README.md) · **한국어** (이 문서)

## 시작

대상 컴퓨터의 터미널에 접근할 수 있는 코딩 에이전트에 이 한 줄을 붙여 넣으세요.

```text
https://github.com/yazzang-homelab/gjc-openrouter-preset-sync-public 이 저장소의 README를 읽고 설치해 줘.
```

이 한 줄로 설치와 정책 초안 생성까지 준비합니다. 유료 평가, 프로필 적용, 기본 모델 변경, 타이머 활성화는
각각 사용자의 별도 명시적 승인이 필요합니다.

<details>
<summary>에이전트가 할 일(절차)</summary>

1. 플랫폼(Linux 권장)과 GJC에 등록된 모델을 참조하는 사용자 프로필이 있는지 확인한다.
2. 격리된 venv를 만들고 `pip install .` 후 그 인터프리터로 기존 `scripts/install.py`를 실행한다.
3. 정책이 없고 등록된 모델이 있으면 `~/.local/bin/gjc-preset-sync init-policy`를 실행한다. 기존 정책은 덮어쓰지 않는다.
4. 설치 내용, 초안 생성 또는 기존 정책 보존 여부, 부족한 전제를 보고한다. 평가나 프로필 변경은 실행하지 않는다.

</details>

<details>
<summary>수동 설치와 상세 안내</summary>

## 빠른 시작

요구 사항: Python 3.10 이상, PyYAML 6.0.2 이상 7 미만, 등록된 모델을 이미 참조하는 사용자 프로필이
하나 이상 있는 [GJC](https://github.com/Yeachan-Heo/gajae-code) 설치([GJC model profiles](https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/models.md) 참조),
Linux(동기화는 권장, 평가기는 `fcntl`/`getuid`/bubblewrap 때문에 필수).
systemd는 선택적인 Linux 스케줄러이며 의존성이 아닙니다.

```bash
git clone https://github.com/yazzang-homelab/gjc-openrouter-preset-sync-public.git
cd gjc-openrouter-preset-sync-public
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python scripts/install.py
~/.local/bin/gjc-preset-sync --help
~/.local/bin/gjc-preset-sync init-policy
```

일어난 일과 일어나지 않은 일:

- 설치기는 CLI 래퍼를 `~/.local/bin/gjc-preset-sync`에, 패키지와 이 문서를 `~/.local/share/gjc-preset-sync`에,
  스킬을 `<agent-dir>/skills/openrouter-preset-sync/SKILL.md`에, systemd **user** unit 파일 두 개를 복사했습니다.
  의존성 설치, 데이터 조회, 프리셋 변경, 타이머 활성화는 하지 않았습니다.
- 래퍼는 `install.py`를 실행한 Python을 사용합니다. 가상환경을 삭제하지 마세요.
- `GJC_CODING_AGENT_DIR` 또는 `install.py --agent-dir`로 GJC 사용자 디렉터리(기본 `~/.gjc/agent`)를 지정합니다.
  사용자 스킬 탐색이 활성화된 **새 세션**에서 `/skill:openrouter-preset-sync`로 호출합니다.
- `init-policy`는 `~/.config/gjc-preset-sync/policy.json`을 만들며, 기존 프로필이 **이미 참조하고 등록도 확인되는**
  selector만 복사합니다. 인증 파일을 읽지 않고 기존 정책을 덮어쓰지 않습니다.

**설치 성공은 적용 가능 상태가 아닙니다.** 생성된 정책은 `"quality": {"status": "draft"}`인 비실행 초안입니다.
아래 두 단계를 마치기 전까지 `plan`은 `policy_unconfigured`를 보고하고 `sync --apply`는 차단됩니다.

### 1단계 — 품질 설정

`~/.config/gjc-preset-sync/policy.json`의 `quality`를 아래 [정책](#정책)의 필수 필드 전체로 채웁니다.
일부만 채우면 검증기가 거부합니다. 설정 generator는 없으며 hash 필드는 같은 절의 도출 함수로 계산합니다.
모든 값을 명시하면 configured로 판정하지만 통계적으로 검증된 문턱이라는 뜻은 아닙니다.

### 2단계 — 확인 증거 생성

인기는 후보를 발견할 뿐이며 다섯 역할과 모든 primary/fallback에 유효한 확인 증거가 있어야 승격됩니다.
평가는 별도 CLI이며 `plan`/`sync`/설치기/타이머가 시작하지 않습니다.

```bash
.venv/bin/python -m gjc_preset_sync.evaluate prepare \
  --manifest /private/manifest.json --policy ~/.config/gjc-preset-sync/policy.json \
  --models ~/.gjc/agent/models.yml --output /private/eval-out
```

`prepare`는 모델을 실행하지 않고 재사용 여부·필요 실행 수·잔여 기간 예산을 보고합니다.
패키지가 설치된 인터프리터(위의 `.venv/bin/python`)로 실행하십시오. 별도로 가상환경을 활성화하지 않았다면 `python3`은 다른 인터프리터를 가리킬 수 있습니다.
실제 실행이 필요하고 비용을 수용한다면 `--approval /private/approval.json`을 추가한 `run`을 사용합니다.
manifest/승인 필드와 격리 전제는 아래 [정책](#정책) 절에 있습니다.

### 3단계 — 미리보기 후 명시적 적용

현재 셸에 `OPENROUTER_API_KEY`를 설정한 상태에서:

```bash
~/.local/bin/gjc-preset-sync tasks
~/.local/bin/gjc-preset-sync plan
```

`tasks`는 실제 관측 태그를, `plan`은 기록할 예정인 프로필을 보여 줍니다. 둘 다 `models.yml`을 바꾸지 않습니다.
결과가 기대와 맞을 때만:

```bash
~/.local/bin/gjc-preset-sync sync --apply
~/.local/bin/gjc-preset-sync status
gjc --mpreset or-auto
```

관리 프로필이 아직 없는 최초 적용에는 `--bootstrap`이 추가로 필요하며 품질 검사를 생략하지 않습니다.
`sync --apply`는 관리 `or-*` 프로필만 기록하고 `config.yml`, 기존 기본 선택, 실행 중인 세션은 바꾸지 않습니다.
한 역할이라도 확인된 후보가 비면 부분 업데이트 없이 기존 프로필 전체를 유지합니다.

문제 해결 표는 [영문 README의 Troubleshooting](README.md#troubleshooting)을 참조하십시오.

## 동작 개요

```text
GET 작업별 7일 점유율 + GET 모델 카탈로그
  → 실제 models.yml에 등록된 허용 모델과 정확하게 연결
  → 도구·컨텍스트·참조 가격 조건 검사
  → 역할별 가중 점유율로 후보 정렬
  → 유효한 역할별 품질 증거 재사용·비교 (없으면 기존 프리셋 유지)
  → 다섯 역할과 모든 primary/fallback의 본평가 통과 확인
  → profiles.or-auto.model_mapping 갱신
```

## 먼저 구분할 것

구현은 공식 Data API의 `GET /api/v1/classifications/task?window=7d`를 사용합니다.
각 작업의 상위 모델에 `tag_usage_share`와 `tag_token_share`를 제공합니다.
**프롬프트를 분류해 주는 API는 아니며, Auto Router 내부 지출 점유율 전체 순위를 반환하는 API도 아닙니다.**

기본값은 `request_share`입니다. `token_share`로 바꿀 수 있지만 이를 `spend_share`로 둔갑시키지 않습니다.
`max_tokens: 1` 추론 호출, 유료 분류 프로브, HTML/비공식 endpoint 스크래핑은 구현하지 않았습니다.
**갱신 코드의 모델 추론 호출 수는 0입니다.** 데이터 API 호출 자체의 과금 여부를 추정해 무료라고 단정하지 않습니다.
실제로 생성한 프리셋으로 GJC를 실행하면 기존 연결의 구독 한도·요금·제공자 정책이 적용됩니다.

## 실제 연동 지점

GJC의 사용자 `profiles` 및 `model_mapping` 형식을 대상으로 합니다.
GJC 본체나 서명된 공식 preset registry는 수정하지 않습니다.
연동 스키마는 아래 공식 근거의 `models-config-schema.ts`를 참조하십시오.
다른 GJC 버전과의 호환성은 해당 버전의 스키마로 확인해야 합니다.

```yaml
profiles:
  or-auto:
    required_providers: []
    display_name: or-auto
    model_mapping:
      default: ["local/alpha:high", "local/beta:high"]
      executor: ["local/beta:high", "local/alpha:high"]
      planner: ["local/alpha:high", "local/beta:high"]
      architect: ["local/alpha:high", "local/beta:high"]
      critic: ["local/beta:high", "local/alpha:high"]
```

위 모델명은 **형식 설명용 가상 모델**입니다. 실제 모델 선택 결과가 아닙니다.
`required_providers: []`는 모든 후보 계정의 동시 인증을 강제하지 않기 위한 것이며,
GJC가 명시된 각 selector의 실제 가용성/인증을 실행 시 해석합니다.
후보 배열은 GJC 자체의 primary/fallback 기능을 사용합니다. 새 API 제공자를 생성하지 않습니다.

## 설치 세부 사항

설치기는 PyYAML이 없는 Python에서는 중단하며 의존성을 자동 설치하지 않습니다. 시스템 Python에 PyYAML이
이미 있다면 가상환경 없이 `sh scripts/install.sh`로도 설치할 수 있습니다. 설치 전 `python3 -m unittest discover -s tests -v`로
오프라인 테스트를 실행할 수 있으나, 테스트 통과가 운영 환경의 설치·실데이터·적용을 의미하지는 않습니다.

v2 정책만 허용하며 v1은 거부합니다. `init-policy` 초안은 `quality: {status: draft}`이고, `plan`은 초안에서
`policy_unconfigured`를 보고하며 `sync --apply`는 종료 코드 2로 차단합니다. 이 도구는 `gjc --default`를 호출하지 않습니다.

## 자동 갱신

타이머는 평가기를 절대 실행하지 않습니다. 증거가 만료되거나 부족하면 현재 프리셋을 보존합니다.

설치기는 systemd **user** unit 파일만 설치합니다. 자동 활성화하지 않습니다.
정상적인 실데이터 `plan`/`sync --apply` 후, 타이머가 쓸 키는
`~/.config/gjc-preset-sync/openrouter.env`에 소유자만 읽는 권한(0600)으로 제공해야 합니다.
파일 형식은 `OPENROUTER_API_KEY=...`이며 실제 키를 Git에 넣지 않습니다.
현재 셸의 환경 변수는 systemd user manager로 자동 전달되지 않습니다.

```bash
systemctl --user daemon-reload
systemctl --user enable --now gjc-preset-sync.timer
systemctl --user list-timers gjc-preset-sync.timer
journalctl --user -u gjc-preset-sync.service -n 30 --no-pager
```

예약은 한국 시간 00:17 / 06:17 / 12:17 / 18:17, 최대 120초 지터입니다.
로그아웃 후에도 동작하려면 해당 컴퓨터의 user manager/linger 정책을 별도로 확인해야 합니다.
타이머는 데이터만 갱신합니다. 저장소 코드를 자동으로 pull하여 실행하지 않습니다.
GitHub Actions는 테스트용이며 사용자 컴퓨터의 models.yml을 수정하는 주체가 아닙니다.

## 정책

### 반복 평가 비용과 품질 증거

새 모델 출시는 평가 실행권한이 아닙니다. 등록·허용·능력 조건을 통과한 역할별 소수 후보만
대상이 됩니다. 같은 연결·모델·요청 effort·GJC 실행파일·과제·채점·격리 조건의 유효한
관측은 재사용합니다. 정책 문턱만 바뀌면 모델 호출 없이 재판정하지만 수집시각은 갱신하지 않습니다.
유효 FAIL도 재사용하며 실패 과제만 재시도하여 성공 결과로 교체하지 않습니다.

별도 CLI는 `.venv/bin/python -m gjc_preset_sync.evaluate prepare|run --manifest <private.json>
--policy <policy.json> --models <models.yml> --output <private-dir>`입니다.
이 모듈 명령은 패키지를 설치한 Python 환경(위 가상환경) 또는 공개 소스 디렉터리에서 실행하십시오.
파일 복사 설치기만 사용한 경우에는 해당 설치의 Python과
`PYTHONPATH="$HOME/.local/share/gjc-preset-sync"`를 함께 지정하십시오.
`prepare`는 실행·승인 소비 없이 재사용 여부, 필요한 실행 수, 잔여 기간 예산을 보고합니다.
신규 `run`에만 `--approval <private-approval.json>`이 필요합니다. 승인 파일은 소유자 전용
0600이어야 하며 실제 호출과 비용은 운영자가 별도로 승인해야 합니다.

- 역할별 1개 선별 과제와 별도 본평가를 사용합니다. 선별 FAIL/UNKNOWN이면 본평가 0회입니다.
- 본평가는 executor 2, critic 3, default 2, planner 2, architect 2개입니다. 모두 기계 채점이며
  작은 artifact 과제의 범위만 측정합니다. 일반 역할 능력이나 tool-use 품질을 보장하지 않습니다.
- 후보의 대상 역할만 평가하고 비교 가능한 incumbent와 나머지 역할의 유효 증거는 재사용합니다.
- 기간별 GJC 프로세스 실행 수·신규 후보 수·예약 시간과 cooldown을 명시해야 합니다.
  새 승인·run ID·재시작으로 사용량을 초기화하지 않습니다. 불명확한 중단은 보수적으로 소비합니다.
- 제한은 GJC launch 기준이지 내부 HTTP 요청 수나 금액 hardcap이 아닙니다. 선택적인 USD
  관측비용 soft-stop도 누락된 사용량·진행 중 청구 때문에 실제 금액 상한을 보장하지 않습니다.
- 증거는 `~/.local/state/gjc-preset-sync/evaluation-state`에 저장합니다. 승인 파일로 상태 경로를
  바꿀 수 없습니다. `sync --evidence-dir`는 기존 증거의 읽기 경로만 지정하며 평가하지 않습니다.

`quality` 설정에는 `required_identity_level: gjc_reported`, `runtime_hash`, `runtime_version`, `harness_hash`,
`connections[정확한 provider/model]`의 `revision/config_hash`, `quota`와 다섯 `roles`가 필요합니다.
역할별 필수 항목은 `coverage`, `suite_hash`, `scorer_hash`, `conditions_hash`, `confirm_cases`, `screen_cases`, `shortlist`,
`min_cases`, `max_failures`, `max_timeouts`, `min_pass_rate`, `evidence_ttl_hours`,
`max_pair_gap_hours`, `paired_rule`의 `min_improvement/max_failure_increase/fallback_degradation`입니다.
quota 필수 항목은 `period_seconds`, `max_launches_per_period`, `max_new_candidates_per_period`,
`max_reserved_wall_seconds_per_period`, `reevaluation_cooldown_seconds`입니다.
모든 값을 명시하면 configured로 판정하지만 통계적으로 검증된 문턱이라는 뜻은 아닙니다.

manifest에는 `version: 1`, 고유 `run_id`, `role`, `stage: screen|confirm`, 정확한 `selector`와
`remote_id`, `binding`, 전체 `cases`, `limits`, `run_timeout_seconds`, `runtime_path`, `agent_dir`,
읽기 전용 원천의 `discovery_tasks/discovery_catalog`가 필요합니다. `binding`은
`quality.expected_binding(models, policy["quality"], role, selector, base, remote_id, effort, stage)`,
과제 집합과 버전은 `eval_suite.cases(role, stage)`, `eval_suite.suite_hash()`, `eval_suite.scorer_hash()`로
도출하십시오. `limits`는 `timeout_seconds/memory_bytes/cpu_seconds/processes/file_bytes/output_bytes`입니다.
`conditions_hash`는 `evaluate.execution_conditions(limits, run_timeout_seconds)`, `harness_hash`는
`quality.harness_hash()`로 도출합니다. 실행기·파서·격리 구현이나 run 시간 제한 변경도 기존 관측을
무효화하지만 자동 재평가를 시작하지는 않습니다. 실제 키나 운영 데이터를 공개 예제에 복사하지 마십시오.

승인에는 `version: 1`, `approval_id`, 전체 manifest의 `manifest_sha256`, `selector`,
`connection_revision`, `role`, `stage`, `max_launches`, `case_timeout_seconds`, `run_timeout_seconds`,
시간대가 있는 `valid_until`, `approved: true`, `acknowledge_no_monetary_cap: true`를 명시합니다.
선택 항목 `observed_cost_soft_stop`은 `{currency: USD, amount: 양수}`입니다.
연결의 `config_hash`는 `quality.config_hash(models, provider, model)`, `manifest_sha256`은
`quality.sha(manifest)`로 계산합니다. 후자는 들여쓴 파일 바이트가 아닌 정규화된 JSON을 해시합니다.

Live 환경은 bubblewrap와 hash로 고정한 독립 실행형 GJC 바이너리가 필요합니다. 해시만 고정한
셸 래퍼는 실제 런타임을 결속하지 못하므로 거부합니다. 별도로 준비한 연결 디렉터리에는
정확히 한 provider/model의 `models.yml`과 선택적 `auth.json`만 허용합니다. 운영 HOME이나
설정 전체를 복사하지 않으며 grader는 네트워크가 없는 별도 격리에서 실행합니다.
provider 네트워크의 목적지 제한은 운영 환경에서 담당해야 하며 bubblewrap 자체는 목적지
allowlist가 아닙니다. 격리가 불가능하면 live 실행을 거부하고 테스트용 실행으로 대체하지 않습니다.

`~/.config/gjc-preset-sync/policy.json`에서 설정합니다. 실제 사용자 정책은 저장소에 넣지 않습니다.

| 항목 | 의미 |
|---|---|
| `profile_id` | 관리할 `or-*` 프리셋 이름. 기존 사용자 이름과 충돌하면 중단 |
| `allowed_selectors` | 기존 로컬 provider/model[:effort]의 정확한 허용 목록 |
| `aliases` | 정확한 로컬 provider/model → 정확한 OpenRouter ID 매핑. 다른 모델로 대체하는 용도가 아님 |
| `metric` | `request_share` 또는 `token_share` |
| `roles.*.tasks` | 관측한 task tag 또는 `code:*` 같은 패턴과 양의 가중치 |
| `roles.*.top_k` | primary와 fallback을 합친 후보 수, 1–8 |
| `roles.*.effort` | 선택 사항. 로컬 thinking 메타데이터가 지원을 확인해 줄 때만 적용 |
| `filters.require_tools` | OpenRouter 도구 지원 + 로컬의 명시적 비호환 설정 확인 |
| `filters.min_context` | 카탈로그와 로컬 contextWindow 모두 충족해야 함 |
| `filters.max_prompt_per_million` | OpenRouter 참조 입력 가격 상한, USD/백만 토큰 |
| `filters.max_completion_per_million` | OpenRouter 참조 출력 가격 상한, USD/백만 토큰 |
| `cache_hours` / `max_age_hours` | 기본 캐시 6시간 / 원본 날짜 최대 72시간 |

가격은 실제 로컬 구독 과금이나 잔여 쿼타가 아닙니다. 출력 가격만으로 요청 총비용도 보장하지 않습니다.
역할별 discovery 점수는 점유율 가중평균입니다. 신규 평가 shortlist는 양의 점유율이 있어야 합니다.
품질을 통과한 적용 후보는 본평가 통과율을 우선하며 점유율은 동점 처리에만 사용합니다.
유효 incumbent는 인기 top-N 밖이어도 유지합니다. 상위 인기 모델의 품질 우월성을 주장하지 않습니다.

초기 역할 정책은 default=agent+코드 생성, executor=코드 생성, planner=agent,
architect=code 전체, critic=debugging입니다. 이는 로컬 정책이며 공식 분류기의 역할 판정이 아닙니다.
`gjc-preset-sync tasks`로 실제 관측 태그를 확인해 조정하세요. 태그가 없거나 한 역할이라도 후보가 비면
부분 업데이트하지 않고 기존 설치 프리셋 전체를 유지합니다.

매핑은 전체 OpenRouter ID가 같거나 로컬 model ID가 OpenRouter의 vendor 제거 ID와 **유일하게 일치**할 때만 인정합니다.
동일한 짧은 ID가 여러 vendor에 있으면 `aliases`를 명시해야 합니다. 유사 이름, 버전 교체, 와이어 호환성을 추측하지 않습니다.
동일 로컬 transport/model의 effort 변형은 중복 fallback으로 넣지 않습니다.
새 출시 모델은 카탈로그에 나타나도 로컬 등록·허용 목록에 없으면 자동 도입하지 않습니다.
새 후보의 사용 승인은 정책 수정으로 명시하세요.

## 백업·롤백·실패 처리

```bash
~/.local/bin/gjc-preset-sync rollback --apply
systemctl --user disable --now gjc-preset-sync.timer
```

관리 프리셋만 되돌립니다. 이후 변경된 다른 프리셋이나 provider를 옛 백업으로 덮어쓰지 않습니다.
복원하는 프리셋도 현재 품질 검사를 통과해야 합니다. 짧은 모델 ID를 별칭 없이 매핑한 경우에는
`rollback --apply --catalog-file <catalog.json>`으로 정확한 카탈로그도 제공하십시오.
다른 프로세스가 관리 프리셋을 수동 수정했으면 중단합니다.
상태/백업은 `~/.local/state/gjc-preset-sync`에 디렉터리 0700, 파일 0600으로 저장하며 최근 10개를 보관합니다.
전체 models.yml 백업에는 기존 비밀 값이 포함될 수 있으므로 외부 공유/커밋하지 마세요.

GJC의 `<models.yml>.lock/info` 잠금 형식에 참여하며 다른 잠금을 제거하지 않습니다.
쓰기 직전 원문 재검사, fsync+atomic rename, pending journal 복구를 사용합니다.
잠금이 남아 있으면 소유 프로세스를 확인해야 하며 도구는 임의로 잠금을 탈취하지 않습니다.
제3자 편집기가 GJC 잠금을 무시하면 매우 짧은 검사-rename 구간의 경쟁까지 완전히 막지는 못합니다.

providers 등 **profiles 외 섹션은 바이트 그대로**, 관리하지 않는 프로필은 의미 그대로 보존합니다.
profiles 섹션 안의 주석/서식은 PyYAML 직렬화로 재배치·소실될 수 있습니다.
앵커/별칭/중복 키와 최상위 flow-style YAML은 잘못 수정하지 않도록 거부합니다.
이 기능 때문에 생성 전 원본 백업을 남깁니다.

## 지출 점유율 스냅샷 가져오기

공식 API의 usage/token 값을 지출 값으로 계산·위장하지 않습니다. 별도로 검증한 지출 스냅샷이 있을 때만:

```bash
gjc-preset-sync plan --tasks-file /private/spend.json --catalog-file /private/models.json --spend-snapshot
```

형식은 `examples/spend-snapshot.schema-example.json`에 있습니다. 그 파일은 가상 값이며 실제 데이터가 아닙니다.
실제 반영에는 추가로 `sync --apply --allow-snapshot-apply`가 필요합니다.
스냅샷은 운영자가 출처를 확인해야 합니다. 이 도구가 OpenRouter의 서명/진위를 검증하는 것은 아닙니다.
지출 데이터의 **자동 수집 어댑터는 제공하지 않습니다.** 예약 갱신의 기본 소스는 공식 request/token 점유율 API입니다.

## 검증

```bash
python3 -m unittest discover -s tests -v
```

테스트는 가상 모델과 격리된 임시 디렉터리를 쓰며 네트워크/추론 호출 없이 실행됩니다.
테스트 통과는 현재 운영 환경의 설치·실데이터 조회·프리셋 적용·타이머 활성화를 의미하지 않습니다.
운영자의 실제 설치 상태나 모델 목록은 공개용 사본에 포함하지 않습니다.

## 소개 페이지와 공개 소스 번들

`site/`는 실제 API나 사용자 설정에 연결되지 않는 정적 소개 페이지입니다.
페이지의 점수 예시는 가상 데이터이며 모델 품질 평가나 현재 순위가 아닙니다.

```bash
python3 scripts/build_site.py
```

산출물은 `dist/site/`의 HTML·CSS·JavaScript·`source.zip`·`SHA256SUMS`입니다.
ZIP에는 `scripts/build_site.py`의 명시적 파일 허용 목록만 들어갑니다.
Git 이력·환경 파일·사용자 설정·캐시·운영 보고서는 디렉터리에 있더라도 복사하지 않습니다.
허용 목록에 새 파일을 추가하기 전에는 그 파일의 공개 적합성을 별도로 검토해야 합니다.
ZIP 내부에도 원본 파일의 `SHA256SUMS`가 들어 있습니다. 체크섬은 파일 일치 확인용이지
배포자의 신원을 인증하는 서명은 아닙니다. 빌드 명령 자체는 배포나 저장소 게시를 수행하지 않습니다.

## 공식 근거

- Auto Router 구조와 메타데이터: https://openrouter.ai/docs/guides/routing/routers/auto-router
- 작업별 Data API, 스키마, 라이선스: https://openrouter.ai/docs/cookbook/administration/data-api
- 공개 task-spend 화면: https://openrouter.ai/rankings#task-spend
- GJC model profiles / fallback 배열: https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/models.md
- GJC profile schema: https://github.com/Yeachan-Heo/gajae-code/blob/main/packages/coding-agent/src/config/models-config-schema.ts

공개 데이터는 OpenRouter에 출처를 표시해야 합니다. 갱신 보고서는 원본 날짜와 CC BY 4.0 표시를 보존합니다.

</details>

## 배포물과 라이선스

이 공개용 사본에는 소스와 가상 테스트 예제만 포함합니다. 사용자 설정·자격증명·운영 보고서·
비공개 Git 이력은 포함하지 않습니다.

- **소프트웨어:** MIT License — [LICENSE](LICENSE) 참조.
- **데이터:** 실행 시 소비하는 OpenRouter 데이터셋은 OpenRouter가 CC BY 4.0으로 제공합니다. 생성 보고서는
  원본 날짜와 출처 표기를 보존합니다. 이 표기는 데이터에 대한 것이며 이 코드의 라이선스가 아닙니다.
