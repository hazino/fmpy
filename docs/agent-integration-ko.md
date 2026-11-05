# 에이전트 연동 구조

`fmpy.scenario`는 사람이 쓰는 시험 실행기이고, `fmpy.scenario_agent`는 **프로그램이
붙는 자리**입니다. 모든 입력과 출력이 JSON이고, 문제는 예외가 아니라 데이터로
돌아오며, 자연어 해석이나 전달받은 코드 실행은 하지 않습니다. 이 모듈 자체는
어떤 모델도 요구하지 않습니다. Ollama/Qwen 같은 로컬 모델은 이 도구들을 호출하는
쪽에 있을 뿐이고, 없어도 전부 동작합니다.

역할은 그대로 분리합니다.

1. 사람 / GUI / 모델이 시나리오 JSON을 만든다.
2. 실행기가 FMU 인터페이스와 JSON을 검증한다.
3. FMPy로 초기화 → Set → doStep → Get → 결과 저장·판정을 수행한다.

## 도구

| 도구 | 하는 일 |
|---|---|
| `tools` | 호출 가능한 도구 목록을 function-calling 형식으로 반환 |
| `schema` | 시나리오 JSON Schema, 실행기 능력, 오류 코드 |
| `describe` | FMU 인터페이스. 쓸 수 있는 변수와 읽을 수 있는 변수를 분리 |
| `structure` | FMU가 선언한 실행 구조. 출력·입력 의존 관계, 상태 수, 선언 타입, 아카이브 내용 |
| `plan` | 선언된 구조에서 **실제로 돌려볼 시나리오**를 도출 |
| `review` | 어떤 선언이 빠졌고 그것이 시나리오 도출에서 무엇을 잃게 하는지 보고 |
| `context` | 시나리오를 쓰는 데 필요한 전부를 한 객체로. 인터페이스 + 스키마 + 능력 + 오류 코드 + 동작하는 예시 |
| `validate` | 네이티브 코드를 띄우지 않고 검증. 모든 문제를 코드와 JSON 경로로 반환 |
| `run` | 검증 후 실행. 판정·요약·발생한 규칙·FMU 로그를 반환. 표 전체는 반환하지 않음 |
| `batch` | 시나리오 파일 여러 개를 같은 FMU에 돌리고 합격/불합격/판정 없음/실행 불가를 종합 |

명령줄은 stdout에 **JSON 객체 하나만** 출력합니다.

```powershell
.\.venv\Scripts\python -m fmpy.scenario_agent tools
.\.venv\Scripts\python -m fmpy.scenario_agent plan     --fmu model.fmu
.\.venv\Scripts\python -m fmpy.scenario_agent review   --fmu model.fmu
.\.venv\Scripts\python -m fmpy.scenario_agent context  --fmu model.fmu
.\.venv\Scripts\python -m fmpy.scenario_agent validate --fmu model.fmu --scenario s.json
.\.venv\Scripts\python -m fmpy.scenario_agent run      --fmu model.fmu --scenario - --output out
```

`--scenario -`는 표준 입력에서 읽습니다. 방금 생성한 JSON을 파일로 떨어뜨리지 않고
그대로 넘길 수 있습니다.

종료 코드: `0` 도구 성공, `1` 시험 불합격(`passed`가 false), `2` 도구가 문제를 보고함
(`ok`가 false). 응답 봉투는 항상 `{"ok": ..., "tool": ...}`이고, 실패 시 `errors`에
`{"code", "path", "message", ...}` 목록이 들어갑니다.

Python에서도 같은 함수를 직접 부릅니다.

```python
from fmpy.scenario_agent import authoring_context, validate, run

context = authoring_context("model.fmu")   # 모델에 줄 근거 자료
checked = validate("model.fmu", scenario)  # 모든 문제를 한 번에
result = run("model.fmu", scenario, output="results/run-001")
```

## 에이전트 루프

1. `context`로 근거를 받는다. 참조 FMU 기준 약 12 KB로 프롬프트에 들어간다.
2. 모델이 시나리오 JSON 하나를 생성한다.
3. `validate`에 넣는다. 실패하면 `code`와 `path`를 보고 그 자리만 고친다.
   한 번의 호출이 **모든** 독립적인 문제를 함께 보고하므로 왕복이 1회로 끝난다.
4. 통과하면 `run`을 호출한다.
5. `summary`로 결과를 읽는다. 표 전체를 읽히지 않는다.

`validate`가 돌려주는 형태:

```json
{"ok": false, "tool": "validate", "errors": [
  {"code": "unknown_variable", "path": "initial.speed_commnd",
   "message": "Unknown variable: speed_commnd",
   "candidates": ["speed_command"]},
  {"code": "off_grid", "path": "events[0].time",
   "message": "Times must lie on the fixed communication grid",
   "start_time": 0.0, "step_size": 0.1}
]}
```

`path`는 시나리오 JSON 안의 위치이고, `code`는 고정된 식별자입니다. 각 코드가 요구하는
수정은 `schema` 도구의 `error_codes`에 들어 있습니다.

## FMU에서 기계적으로 뽑을 수 있는 것

`plan`과 `structure`는 `modelDescription.xml`이 **선언한** 내용만 사용합니다.
FMU를 열면 다음이 확보됩니다.

- 변수의 이름·자료형·causality·variability·단위·설명·시작값·min/max
- **ModelStructure의 출력↔입력 의존 관계** 와 `dependenciesKind`.
  어떤 입력을 흔들면 어떤 출력이 움직이는지가 선언되어 있습니다.
- 연속 상태 수, 미분 수, **이벤트 지시자 수**
- `typeDefinitions`: quantity, 단위, Enumeration 항목의 **이름과 값**
- `unitDefinitions`, `logCategories`
- `defaultExperiment`의 시작/종료/허용오차/스텝 크기
- Co-Simulation 능력 플래그(가변 간격 허용, 상태 저장·복원, 출력 미분 차수 등)
- `variableNamingConvention`이 `structured`면 이름의 첫 구간이 하위 시스템을 드러냅니다
- 아카이브 구성: `sources/`, `documentation/`, `resources/`에 무엇이 들어 있는지

의존 관계에는 FMI 2.0의 구분이 있어 그대로 전달합니다.
`depends_on`이 `null`이면 FMU가 속성을 생략한 것이고, 규격상 **모든 변수에 의존할 수
있다**는 뜻입니다. 빈 배열이면 어떤 입력에도 직접 의존하지 않는다, 즉 값이 모델 내부
상태에서 나온다는 뜻입니다.

### 원본 소스 코드는 어디까지 쓸 수 있는가

FMI 2.0 FMU는 `sources/`에 C 소스를 함께 담을 수 있습니다. 실제로 공식 Reference FMU는
7개, Modelica로 내보낸 `Drivetrain.fmu`는 122개의 소스 파일을 담고 있습니다.
`structure` 도구가 그 목록을 `archive.source_files`로 알려주므로 에이전트가 존재를 알고
읽을 수는 있습니다. 다만 이 저장소는 **소스를 파싱해서 시험을 도출하지 않습니다.**
이유는 세 가지입니다.

- **없을 수 있습니다.** 소스 포함은 선택이고, 상용 FMU는 대개 바이너리만 담습니다.
  FMPy에는 소스를 제거하는 기능(`remove_source_code`)도 있습니다. 소스에 의존하는
  절차는 정작 중요한 FMU에서 무너집니다.
- **구현일 뿐입니다.** 도구가 생성한 FMU의 소스는 솔버 글루, 유틸리티, 이름이 바뀐
  변수로 이루어진 기계 생성 코드입니다. 거기서 읽히는 것은 "무엇을 하는가"이고,
  시험이 필요한 것은 "무엇이어야 하는가"입니다. 둘은 다릅니다.
- **더 좋은 출처가 있습니다.** 사용자는 기존 소프트웨어를 FMU로 포팅하는 쪽이므로
  원본 소스와 요구사항은 FMU 밖에 이미 있습니다. 생성된 C를 역해석하는 것보다
  그 자료와 선언된 구조를 근거로 주는 편이 정확합니다.

사람이 쓴 설명이 들어 있을 가능성이 높은 `documentation/`과, FMU가 읽는 설정·데이터가
들어 있는 `resources/`는 목록으로 함께 보고합니다. 특정 거동의 원인을 추적할 때
`sources/`를 읽는 것은 유효하지만, 시험 계획의 출처로 삼지는 않습니다.

## 여러 시나리오를 한 번에 돌리고 판정을 종합하기

`fmpy.scenario_agent.run_many` (CLI: `batch`, GUI: **여러 시나리오 실행...**)는
시나리오 파일 여러 개를 같은 FMU에 대해 독립적으로 돌리고 하나의 요약으로
모읍니다. 하나가 깨져도(JSON이 아니거나, 검증 실패거나, FMI 호출이 거부되거나)
나머지는 계속 실행됩니다 — 배치 전체가 멈추지 않습니다.

```powershell
.\.venv\Scripts\python -m fmpy.scenario_agent batch `
  --fmu examples/agent/pid_demo/PID.fmu `
  --scenarios examples/agent/pid_demo/scenarios `
  --output scenario-results/batch --report
```

`--scenarios`에 디렉터리를 주면 그 안의 `*.json`을 전부 모읍니다. `--report`는
JSON 대신 사람이 읽는 Markdown 요약(합격/불합격/판정 기준 없음/실행 불가 개수
+ 시나리오별 한 줄)을 출력합니다. 종료 코드: 0 전부 정상(불합격 없음),
1 불합격 있음, 2 실행조차 못 한 것이 있음. PID 데모 7개로 실제 확인한 결과:

```
7개 중 합격 5 · 불합격 0 · 판정 기준 없음 1 · 실행 불가 1
```

(`02`는 판정 기준이 없는 관찰용 시나리오, `07`은 의도적으로 초기화에서
거부되는 시나리오 — 둘 다 설계된 결과입니다.) `output`을 주면
`<폴더>/<파일이름>/`마다 개별 `report.json`/`results.csv`가, 최상위에
`summary.json`이 저장됩니다.

GUI에서는 `ScenarioDialog`(시나리오 창)의 **"여러 시나리오 실행"** 탭이 이
함수를 그대로 부릅니다. 과거에는 별도 팝업 창이었지만 지금은 시나리오 창
자체에 통합된 탭이고, FMU는 그 창 맨 위의 FMU 입력 칸을 같이 씁니다
(`fmpy.gui.scenario_batch.ScenarioBatchDialog` — 이름은 "Dialog"지만 실제로는
탭에 끼워 넣을 수 있는 `QWidget`). 메인 창 툴바의 **"여러 시나리오 실행..."**
도 같은 창을 열어 이 탭으로 바로 이동합니다. 파일을 체크박스로 고르고,
실행 중 각 행이 실시간으로 갱신되고, 끝나면 같은 요약이 위쪽에 뜨고 Markdown
보고서를 저장할 수 있습니다. `ScenarioProposalsDialog`(제안 보기, 지금도
별도 팝업)와 다른 점: 제안 보기는 `plan()`이 XML만 보고 만든 것(판정 기준
없음)을 보여주고, 이 배치 실행기는 **사용자가 이미 작성한, 보통 판정 기준이
있는** 시나리오 묶음을 돌립니다.

## GUI 챗봇 — fmpy 실행 중에 바로 자연어로 요청하기

시나리오 창의 **"에이전트에게 요청"** 탭(메인 창 툴바의
**"에이전트에게 요청..."**도 같은 창을 열어 이 탭으로 이동)입니다. 역시
`fmpy.gui.agent_chat.AgentChatDialog`이지만 지금은 별도 창이 아니라 시나리오
창에 통합된 탭이고, FMU는 그 창 맨 위 칸을 같이 씁니다. 백엔드(Ollama 로컬
/ Claude API)와 모델을 고르고, 채팅창에 자연어로 요청하면:

1. `context`로 FMU 인터페이스를 가져와 프롬프트를 만든다
2. 모델이 시나리오 JSON을 생성한다
3. `validate`가 실패하면 오류를 모델에 돌려주고 다시 생성한다(최대 시도 횟수까지)
4. 통과하면 `run`으로 실제 FMU를 실행한다
5. 판정·샘플 수를 대화창에 표시하고, 결과 그래프 탭에 그리고,
   `<결과폴더>/turn-NNN-HHMMSS/`에 `results.csv`/`report.json`/`scenario.json`을 저장한다

GUI는 아래 `fmpy.scenario_backends` 모듈을 그대로 호출합니다 — CLI
스크립트(`ollama_loop.py`/`claude_loop.py`)와 완전히 같은 코드 경로이고,
GUI에서만 되는 특별한 동작은 없습니다. 백그라운드 스레드에서 실행되며
**중지** 버튼으로 취소할 수 있습니다(다음 턴 경계에서 멈춤).

### 에이전트 설정 (`fmpy.gui.agent_settings`)

채팅 탭의 **설정...** 버튼이 `AgentSettingsDialog`를 엽니다. 저장할 수 있는
항목: 기본 백엔드, Ollama Host/기본 모델/최대 재시도 횟수, Claude 기본
모델/API 키(선택)/최대 턴 수, 기본 결과 폴더. **저장**을 누르면 즉시 채팅
탭에 반영되고(백엔드·모델·결과 폴더가 바로 바뀜), `QSettings`로 이 PC의
사용자 프로필 아래 **INI 파일**에 저장됩니다(Windows 레지스트리가 아님 —
`fmpy.gui.agent_settings._settings().fileName()`으로 정확한 경로 확인
가능). 다음에 창을 열 때도 유지됩니다.

API 키는 선택이고, 비워두면 Claude 백엔드가 환경 변수 `ANTHROPIC_API_KEY`를
그대로 읽습니다(`scenario_backends.run_with_claude`의 기본 동작, CLI와
동일). 입력하면 그 설정 파일에 **평문으로 저장**됩니다 — 입력란 툴팁에
그렇게 적어 뒀고, 원치 않으면 비워두고 환경 변수를 쓰는 쪽을 권합니다.
시나리오·보고서·로그 어디에도 API 키가 나타나지 않습니다.

## 실제로 연결해 돌려본 결과 (Ollama) / 아직 실행 검증 안 한 것 (Claude API)

`examples/agent/ollama_loop.py`: 자연어 요청 → `context` → 모델 호출 →
`validate`로 교정 → `run`까지 도는 실제 코드입니다. 이 PC에서 돌고 있는
Ollama 0.40.4 + `gemma:2b`(2B, completion 전용, tool-calling 미지원)로
**실제로 실행해 확인**했습니다.

```powershell
python examples/agent/ollama_loop.py --fmu examples/agent/pid_demo/PID.fmu `
  --request "Step the setpoint from 0 to 2 at time 0.2 ... check output stays between -10 and 10." `
  --model gemma:2b
```

**솔직한 결과**: 루프 자체(파싱→검증→교정→실행)는 두 번 다 1회 시도로 통과해
정상 작동했습니다. 그런데 `gemma:2b`는 요청에서 **숫자 하나**(setpoint 값)는
반영했지만, `outputs`에 `integrator_state` 추가하기, `checks`의 변수·한계값
바꾸기, `name`/`description` 채우기 같은 **구조적 지시는 따르지 못하고
예시 JSON을 거의 그대로 돌려줬습니다.** 즉 검증·실행 배선은 문제없이
동작하지만, 2B급 completion-only 모델에게 복잡한 시나리오 작성을 맡기기에는
무리라는 것이 실측 결과입니다. 더 큰 모델(`qwen2.5` 등 tool-calling 지원)로
바꾸거나, Ollama의 `/api/chat` `tools=` 파라미터에 `scenario_agent.TOOLS`를
그대로 넘기는 네이티브 tool-calling 방식으로 바꾸면 나아질 가능성이 높습니다
(이 스크립트는 prompt-and-parse 방식이라 `format="json"`만 쓰고 있습니다).

`examples/agent/claude_loop.py`: 같은 루프를 Anthropic Messages API의
네이티브 tool-calling(`scenario_agent.TOOLS`를 `input_schema`로 그대로 전달)
으로 구현했습니다. **이 환경에는 `anthropic` 패키지와 `ANTHROPIC_API_KEY`가
없어 실제 API 호출은 검증하지 못했습니다** — `python -m py_compile`과
`--help`/의존성 누락 시 종료 코드 2까지만 확인했습니다. API 키가 있는 환경에서
`pip install anthropic && export ANTHROPIC_API_KEY=...`로 첫 실행을 직접
확인해 주셔야 합니다.

두 스크립트 모두 모델이 실제로 FMU를 건드리는 일은 없습니다 — 모델은 JSON
텍스트만 내놓고, `validate`/`run`은 항상 이 저장소의 코드가 실행합니다.

## GUI에서 제안을 고르고 결과를 확인하기

실행을 자동화하지 않고, **사람이 제안을 보고 고른 것만** 돌려서 "포팅한 코드가
가상환경에서 제대로 동작하는지" 직접 확인하고 싶다면 GUI를 씁니다. `plan` 도구가
하는 일을 명령줄이 아니라 화면으로 제공하는 것이 `fmpy.gui.scenario_proposals.
ScenarioProposalsDialog`입니다.

시나리오 창(**Scenario...**)에서 FMU만 선택한 상태로 **시나리오 제안 보기...**를
누르면 `plan`이 만든 제안이 표로 나옵니다. 각 행은 검증을 통과한 완전한 시나리오를
담고 있고 기본은 모두 체크되어 있습니다. 체크를 풀어 원하는 것만 남기고
**선택 실행**을 누르면 그것들만 순서대로 실제 FMU에 돌립니다(백그라운드 스레드,
중지 가능). 행을 클릭하면 아래 패널에 그 제안의 근거·대상 입력·기록 출력·
필요한 도메인 정보가 나오고, 실행한 뒤에는 종료 이유·샘플 수·FMU 오류/경고 건수·
저장 위치가 추가됩니다. 결과는 `결과 폴더` 아래 `<제안 id>/<실행 시각>/`에
`results.csv`, `report.json`, `scenario.json`으로 남아 CLI 결과와 똑같이 다룰
수 있습니다.

`validate`가 실패한 제안(지금 참조 FMU들에서는 발생하지 않지만, 다른 FMU나
향후 추가될 소스 코드 기반 제안에서는 생길 수 있음)은 기본적으로 체크가 풀려
있고 왜 거부됐는지 상세 패널에 표시됩니다. 여기서도 판정은 만들어지지 않습니다 —
표는 "돌아간다/안 돌아간다"와 "무슨 값이 나왔다"까지만 보여주고, "그 값이 맞다"는
사용자가 직접 결정합니다.

## 뽑을 수 없는 것

FMU는 **자기가 무엇을 해야 하는지 말하지 않습니다.** 선언된 min/max는 모델이 유효한
범위이고 합격 기준이 아닙니다. 따라서 다음은 기계적으로 도출되지 않으며 도메인 자료가
있어야 합니다.

- 합격·불합격 기준 (`checks`)
- 운전 제약, 정상 동작의 의미, 안전 한계
- 변수 의미와 단위의 공학적 해석
- 이 모델의 동특성에 맞는 통신 간격. FMU는 기본 실험값만 말하고 그것이 적절한지는
  말하지 않습니다.

그래서 `plan`의 제안에는 `checks`가 **없습니다**. 대신 각 제안의
`needs_from_domain`에 무엇을 받아와야 하는지 적습니다. 실행은 되지만 `passed`는
`null`입니다. 실행 성공을 시험 합격으로 쓰지 않기 위한 의도된 결과입니다.

## plan이 만드는 제안

각 제안은 `{"id", "title", "rationale", "covers", "needs_from_domain", "scenario", "valid"}`
구조이고, `scenario`는 그 FMU에 대해 이미 검증을 통과한 완전한 시나리오입니다.

| id | 내용 |
|---|---|
| `smoke` | 모든 입력을 알려진 값으로 고정하고 선언된 출력을 전부 기록. 여기서 실패하면 거동 이견이 아니라 FMU가 이 격자나 시작값을 거부한 것 |
| `step-<입력>` | 그 입력을 선언된 하한에서 상한으로 한 번 계단 변경하고, **그 입력에 의존한다고 선언된 출력**을 기록 |
| `dependency-coverage` | 입력을 각자 다른 통신 시점에 차례로 변경. 한 번의 실행으로 선언된 입력→출력 간선을 모두 건드림. 아무것도 바꾸지 않는 입력이나, 바꾸면 안 되는 것을 바꾸는 입력을 찾는 용도 |
| `parameters-at-declared-bounds` | 경계가 선언된 파라미터를 모두 한계값으로 두고 실행. 파라미터는 초기화 전에만 설정되므로 입력만 흔드는 실행으로는 덮이지 않음 |

격자는 FMU의 `defaultExperiment`를 우선 쓰고, 없으면 구간을 50등분합니다. 제안끼리
같은 격자를 공유하므로 결과를 나란히 비교할 수 있습니다. `notes`에는 이 계획이 모르는
것(판정 기준, 이벤트 지시자가 있어 간격이 부족할 수 있다는 점, 의존 관계가 보수적으로
선언되었을 수 있다는 점 등)을 적습니다.

참조 FMU 5종(Feedthrough, BouncingBall, Stair, Drivetrain, Controller)에서 생성된
제안 19건 중 19건이 검증을 통과하고 실행됩니다.

## 보고서 읽는 법

`run`은 샘플 표를 돌려주지 않습니다. 표는 최대 100만 행이 될 수 있고 읽는 쪽이 사람이든
모델이든 그대로는 쓸 수 없습니다. 대신 다음이 들어갑니다.

- `summary.outputs[변수]`: 수치는 min/max와 그 시각, 첫값·끝값·평균·비유한값 개수.
  Boolean/String은 변화 시점 목록(최대 20개)과 Boolean은 true/false 개수, String은
  서로 다른 값 목록.
- `checks[]`: `passed`, `first_failure_time`, `failure_count`, 그리고 **`margin`**.
  margin은 통과했으면 한계까지 남은 여유, 실패했으면 넘어간 최대 폭(음수)입니다.
  `worst`와 `worst_time`이 그 지점을 가리킵니다.
- `rule_events[]`: 규칙이 **왜** 발생했는지. 발생 시각, 조건, 그 시점의 실제 값
  (`trigger.actual`), 적용한 입력, 종료 여부.
- `fmu_log[]`: FMU가 자기 로거로 보낸 메시지를 시각·등급·카테고리와 함께 최대 200건.
  `summary.log_errors`/`log_warnings`로 개수만 먼저 볼 수 있습니다.
- `termination`: `stop_time`(끝까지 실행), `rule`(규칙이 중지), `fmu_terminated`
  (FMU가 스텝을 discard하며 종료를 요청. 수집한 결과와 판정은 유지됩니다).
- `passed`: `checks`가 없으면 `null`.

`run`에 `include_samples=N`을 주면 앞쪽 N행을 함께 받습니다. 짧은 추적을 직접 볼 때만
씁니다. `output`을 주면 `results.csv`, `report.json`, `scenario.json`을 쓰고 경로를
`artifacts`로 돌려줍니다.

## 추적용 메타데이터

시나리오에 선택 항목으로 `name`, `description`, 그리고 자유 형식 `metadata`를 둘 수
있고, `events`/`rules`/`checks`의 각 항목에는 `id`와 `description`을 붙일 수 있습니다.
`metadata`는 보고서에 그대로 보존되므로 생성한 모델 이름, 프롬프트, 추적 id를 남길 수
있습니다. `id`는 `rule_events`와 `checks`에 그대로 나타나므로 보고서가 어떤 의도였는지를
가리킵니다. `id`는 각 배열 안에서 유일해야 합니다.

## 기존 CSV 입력 기능과의 차별점

FMPy에는 원래 `simulate_fmu(input=...)`로 CSV 시계열을 입력하는 기능이 있습니다
(`fmpy.util.read_csv`, `fmpy.simulation.Input`). 시나리오 JSON은 그것을 대체하는 게
아니라 다른 일을 합니다.

| | CSV 입력 | 시나리오 JSON |
|---|---|---|
| 성격 | 자극(stimulus) 표 | 시험 명세: 자극 + 반응 + 판정 |
| 자료형 | 모든 열을 float로 읽음. String 입력 불가 | Real/Integer/Enumeration/Boolean/String을 선언 타입대로 검증 |
| 입력 변화 | 조밀한 시계열. 연속 Real은 **선형 보간**, discrete/tunable은 hold하며 시간 이벤트 생성 | 희소한 이벤트. 값은 다음 변경까지 hold. **보간 없음** |
| 파라미터 | 불가. `start_values` 인자로 따로 전달 | `initial`에서 fixed/tunable 파라미터까지 설정 |
| 폐루프 반응 | 없음 | `rules`로 조건 충족 시 입력 변경 또는 조기 종료 |
| 합격 판정 | 없음 | `checks` → `passed`, 여유(`margin`)까지 보고 |
| 사전 검증 | 거의 없음. 누락 입력에 경고만 | 네이티브 코드 실행 전 전체 검증, 모든 오류에 코드와 경로 |
| 시간 격자 | CSV 격자와 출력 격자가 달라도 됨 | 종료·이벤트 시각이 `step_size`의 정수배여야 함 |
| FMI 범위 | FMI 1/2/3, Model Exchange와 Co-Simulation | FMI 2.0 Co-Simulation만 |
| 재현성 | 입력 표만 남음 | 시나리오·보고서·결과가 한 폴더에 함께 남음 |
| 에이전트 적합성 | 수천 줄의 숫자. 의도를 표현하지 못하고 검증 피드백이 없음 | 의도가 구조로 남고, 스키마와 오류 코드로 교정 가능 |

요약하면 **CSV는 "무엇을 넣을지"만 적고, 시나리오 JSON은 "무엇을 넣고, 무엇을 보고,
어떻게 반응하고, 무엇이 합격인지"를 적습니다.** 판정과 반응이 필요 없는 단순 재생이면
CSV가 더 짧고, FMI 3.0이나 Model Exchange, 그리고 **선형 보간이 필요한 램프 입력**은
현재 CSV 경로만 지원합니다.

## 두 경로 검토: 완성된 FMU를 읽기 vs 원본 소스에서 빌드하기

시나리오를 얻는 길은 두 가지입니다. 현재 저장소에서 실제로 무엇이 되는지 측정한
결과입니다. 전제: 원본 소스에는 FMU를 빌드하는 CMakeLists가 이미 있습니다.

### 경로 A — 완성된 FMU를 읽어 시나리오를 뽑는다 (지금 구현됨)

`modelDescription.xml`이 선언한 구조로 시나리오를 도출합니다.
`structure`와 `plan` 도구가 이 일을 합니다.

- **동작한다.** 참조 FMU 5종에서 제안 19건 중 19건이 검증을 통과하고 실행됩니다.
- FMU를 누가 만들었는지 상관없습니다. 바이너리만 든 상용 FMU도 됩니다.
- 얻는 것: 출력↔입력 의존 관계, 상태·이벤트 지시자 수, 선언 타입과 단위,
  Enumeration 항목 이름, 기본 실험 격자, CS 능력 플래그.
- 한계: **선언이 빈약하면 계획도 빈약하다.** 설명·단위·범위·의존 관계가 없으면
  제안은 자극 수준만 남습니다. 그리고 합격 기준은 어떤 경우에도 나오지 않습니다.

### 경로 B — 원본 소스에서 FMU를 빌드하고, 같은 명세로 시나리오까지 만든다

전제대로 원본에 CMakeLists가 있으면 **FMPy의 FMU 생성 기능은 필요하지 않습니다.**
사용자 빌드가 FMU를 만들고, 그 FMU를 경로 A의 도구가 그대로 읽습니다. 체인은
`cmake --build` → `.fmu` → `review` → `plan` → `validate` → `run`입니다.

여기서 바뀌는 것은 도구가 아니라 **선언의 소유권**입니다. `modelDescription.xml`을
사용자가 통제하므로, 경로 A의 유일한 한계인 "선언이 빈약하면 계획도 빈약하다"를
스스로 없앨 수 있습니다. 변수 설명·단위·min/max·ModelStructure 의존 관계를 제대로
선언하면 `plan`이 바로 더 나은 제안을 만듭니다. 인터페이스 선언 하나가 FMU와
시험 시나리오의 공통 출처가 됩니다.

그래서 이번에 `review` 도구를 추가했습니다. 어떤 선언이 빠졌고 그것이 시나리오
도출에서 무엇을 잃게 하는지, 무엇을 선언하면 되는지를 심각도 순으로 보고합니다.
예를 들어 공식 Feedthrough는 `missing_unit` 6건, `missing_range` 6건,
`missing_description` 14건, `defaultExperiment`의 `stepSize` 누락이 나오고,
`Drivetrain`은 `tau`가 어떤 출력에도 의존 관계로 선언되지 않은 것이 잡힙니다.

### FMPy의 FMU 생성 기능은 이 전제에서 왜 안 쓰는가

참고로 FMPy에도 FMU를 만드는 길이 있습니다 — `fmpy.template.create_fmu`,
`fmpy.build.build_platform_binary`, GUI의 **New FMU...**. 그런데 측정해 보면
이 전제에서는 쓸 이유가 없습니다.

1. 생성되는 FMU가 **FMI 3.0**입니다. `templates/modelDescription.xml`에
   `fmiVersion="3.0"`이 하드코딩되고 `model.c`는 `fmi3` API로 쓰여 있습니다.
   시나리오 실행기는 FMI 2.0 CS 전용이라 `describe`가 `supported: false`,
   `plan`은 `unsupported_fmu`로 거부합니다. 실제로 그렇게 나오는 것을 확인했습니다.
2. 변수가 **Float64 뿐**입니다. 단위·min/max·`typeDefinitions`·Enumeration·
   Boolean·String이 생성되지 않습니다.
3. `ModelStructure`에 **`dependencies` 속성을 쓰지 않습니다.** 읽으면
   `depends_on`이 `null`(= 모든 변수에 의존 가능)로 나옵니다. 경로 B의 최대
   장점이 바로 빠집니다.
4. `model.c`는 **뼈대**입니다(`getContinuousStateDerivatives`가
   `UNUSED(derivatives)`). FMPy는 임의의 소스를 FMU로 번역해 주지 않습니다.

즉 FMPy의 생성 기능은 "FMI 래퍼가 없는 사람"을 위한 것이고, 전제대로 CMakeLists가
이미 있다면 그 빌드를 쓰는 게 맞습니다.

### 결론

**두 경로는 배타적이지 않습니다.** 전제대로라면 경로 B는 "경로 A + 선언을 내가
고칠 수 있다"이고, 도구는 하나로 충분합니다. 권장 순서:

1. 사용자 CMakeLists로 FMU를 빌드한다.
2. `review`로 선언의 빈 곳을 확인하고 `modelDescription.xml`을 보강한다.
   의존 관계와 범위·단위·설명이 가장 값이 큽니다.
3. `plan`으로 제안을 받아 `run`으로 돌린다.
4. 합격 기준(`checks`)만 요구사항 문서에서 채운다. 이건 어느 경로로도
   FMU에서 나오지 않습니다.

남은 선택지는, 사용자 빌드를 도구 계층에서 직접 호출하는 `build` 도구를 둘지
여부입니다. 빌드 명령과 산출물 경로는 저장소 구조에 달려 있어 아직 만들지
않았습니다. 사용자 저장소 구조를 받으면 추가합니다.

## 아직 없는 것

- 램프/선형 보간 입력. 현재 시나리오는 계단 변경만 표현합니다. CSV 입력 경로가 가진
  유일한 기능적 우위이고, 실제 장비 시험에서 가장 먼저 필요해질 항목입니다.
- 조건 유지 시간, 조건 충족 후 지연, 복합 조건(and/or), 규칙 반복
- 구간 판정, 정착 후 판정, 목표값 도달 판정, 변화율 제한 판정
- 가변 통신 간격, 롤백, 비동기 stepping, 여러 FMU 결합, 단위 변환
- 모델 호출 자체. 프롬프트를 만들고 모델에 보내는 코드는 이 저장소에 없습니다.
  `context` → 생성 → `validate` → `run` 루프를 바깥에서 돌리면 됩니다.
