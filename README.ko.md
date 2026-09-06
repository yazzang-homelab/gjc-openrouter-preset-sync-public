# GJC OpenRouter Preset Sync

OpenRouter의 작업별 모델 점유율을 읽어 **GJC의 사용자 모델 프리셋**을 갱신하는 스킬/CLI입니다.
GJC 본체나 서명된 공식 preset registry를 수정하지 않습니다.

이 공개용 사본에는 소스와 가상 테스트 예제만 포함합니다. 사용자 설정·자격증명·운영 보고서·
비공개 Git 이력은 포함하지 않습니다. 소프트웨어 라이선스는 아직 지정되지 않았으며,
공개 열람 자체가 별도의 사용·수정·재배포 라이선스를 부여하지는 않습니다.
아래 CC BY 4.0 표시는 OpenRouter 데이터의 출처 표기이며 소프트웨어 라이선스와는 별개입니다.

```text
GET 작업별 7일 점유율 + GET 모델 카탈로그
  → 실제 models.yml에 등록된 허용 모델과 정확하게 연결
  → 도구·컨텍스트·참조 가격 조건 검사
  → 역할별 가중 점유율로 후보 정렬
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

## 설치

Python 3.10 이상과 PyYAML 6.0.2 이상(7 미만)이 필요합니다.
설치기는 의존성 설치, 데이터 조회, 기본 프리셋 변경, 타이머 활성화를 하지 않습니다.

공개용 소스를 내려받아 압축을 해제한 뒤, `pyproject.toml`이 있는 디렉터리에서 실행하십시오.

```bash
python3 -m unittest discover -s tests -v
sh scripts/install.sh
~/.local/bin/gjc-preset-sync init-policy
```

의존성이 없다면 별도 가상환경에 설치한 후 같은 Python으로 설치기를 실행하세요.

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python scripts/install.py
```

가상환경으로 설치했다면 해당 가상환경을 삭제하지 마세요. 래퍼가 그 Python을 사용합니다.
`GJC_CODING_AGENT_DIR` 또는 설치기의 `--agent-dir`로 GJC 사용자 디렉터리를 지정할 수 있습니다.
스킬은 해당 agent 디렉터리의 `skills/openrouter-preset-sync/SKILL.md`에 설치됩니다.
GJC의 사용자 스킬 탐색이 활성화된 **새 세션**에서 `/skill:openrouter-preset-sync`를 호출합니다.

## 첫 갱신

`init-policy`는 현재 사용자 프리셋에서 **이미 참조 중이고 등록도 확인되는 selector만** 정책에 복사합니다.
인증 파일이나 키를 읽지 않습니다. 기존 정책이 있으면 덮어쓰지 않습니다.

현재 셸에 `OPENROUTER_API_KEY`를 안전하게 설정한 상태에서:

```bash
~/.local/bin/gjc-preset-sync plan
~/.local/bin/gjc-preset-sync sync --apply
~/.local/bin/gjc-preset-sync status
gjc --mpreset or-auto
```

`plan`과 `sync`의 기본 동작은 models.yml을 바꾸지 않는 미리보기입니다.
`sync --apply`만 관리 프리셋을 기록합니다. `config.yml`, 기존 기본 선택,
실행 중인 세션의 모델은 바꾸지 않습니다. 이 도구는 `--default`를 호출하지 않습니다.

## 자동 갱신

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
역할별 후보 점수는 매칭된 작업들에서의 점유율 가중평균입니다. 공개 top-N 밖은 0으로 취급하고
관측된 양의 점유율이 없는 모델은 후보에서 제외합니다. 상위 모델의 품질 우월성을 주장하지 않습니다.

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
