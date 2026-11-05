# 작업 로그 및 이어하기 안내

마지막 기록: 2026-11-05 (Asia/Seoul)

## 최신 변경: 에이전트 연동 구조와 FMU 구조 기반 시나리오 도출 (2026-11-04, Claude)

### 사용자 요청

1. "에이전트를 연동할 수 있는 구조로 잡아달라."
2. "지금 시나리오 구축된 게 그 방향과 맞는지 확인해달라."
3. "기존 CSV 로드 기능과 시나리오 JSON 기능의 차별점을 확인해달라."
4. "FMU를 로드한 뒤 에이전트가 실행 구조나 원본 코드를 보고 시나리오 계획을
   추출할 수 있는지, 실제 테스트에 돌려볼 시나리오까지 뽑을 수 있는지."

### 2번에 대한 진단 (고치기 전 상태)

맞는 부분: JSON만이 인터페이스이고 자연어 해석·임의 코드 실행이 없다. 네이티브
실행 전에 검증한다. 고정 격자라 재현된다. 판정(`passed`)과 실행 성공을 구분한다.

맞지 않던 부분 — 이번에 고친 것:

- 검증 오류가 문장 하나뿐이고 위치·코드가 없으며 **첫 오류에서 멈췄다.**
  프로그램이 고치려면 왕복이 오류 개수만큼 필요했다.
- 형식의 기계 판독 계약(JSON Schema, 능력 선언)이 없어 산문 문서를 추측해야 했다.
- 결과가 최대 100만 행 표라서 호출자가 읽을 수 없었다. 요약이 없었다.
- 안정된 프로그램 진입점이 없었다. CLI가 `"Scenario is valid"` 같은 문장을
  보고서 JSON과 같은 stdout에 섞어 냈다.
- `fired_rules`가 인덱스뿐이어서 왜 발생했는지 알 수 없었다. 항목에 `id`나
  설명을 달 수 없었고(`fields()`가 미지 키를 거부) 의도를 기록할 자리가 없었다.

### 추가·변경한 파일

- `src/fmpy/scenario.py` (대폭 수정)
  - `ScenarioError`(code/path/extra), `ScenarioValidationError`(전체 목록),
    경로 스택을 가진 `Problems` 수집기. 검증이 **독립적인 문제를 한 번에 전부**
    보고한다. 경로 예: `events[2].set.u`, `rules[0].when.op`.
    변수명 오타에는 `difflib` 후보를 `candidates`로 붙인다.
  - 선택 메타데이터: 최상위 `name`/`description`/`metadata`,
    `events`/`rules`/`checks` 항목의 `id`/`description`. `id`는 배열 내 유일.
  - 보고서 확장: `summary`(출력별 min/max와 그 시각, 첫·끝·평균, Boolean/String은
    변화 시점과 분포), `rule_events`(발생 시각·조건·`trigger.actual`·적용값·중지),
    `checks[]`에 `margin`/`worst`/`worst_time`/`failure_count`, `fmu_log`.
  - `LogCapture`: FMU 자체 로그 메시지를 최대 200건 수집. 네이티브 로거 프록시로
    varargs를 포맷해 받는다. 기존에는 stdout으로만 나가 GUI에서 안 보였다.
  - 일괄 I/O: 출력을 FMI 종류별로 묶어 샘플당 변수 개수만큼 하던 FFI 호출을
    종류 개수만큼으로 줄였다. 규칙 변수는 이벤트 적용 **후** 별도로 읽어
    기존 평가 순서를 유지한다.
  - `fmi2Discard` + `fmi2Terminated`를 조기 종료로 처리
    (`termination: "fmu_terminated"`). FMPy 본체 `simulate_fmu`와 같은 방식.
    이전에는 `FMICallException`으로 터져 수집한 결과가 전부 버려졌다.
  - `instantiate` 실패 시 `freeLibrary()`를 호출한다. 안 하면 Windows에서 DLL이
    임시 폴더를 잠가 `PermissionError`가 진짜 원인을 덮었다.
  - Boolean 출력을 Python `bool`로 보고한다(이전 1/0). **동작 변경**: CSV에
    `True`/`False`로 적힌다. 시나리오의 Boolean 표기와 일치시킨 것이다.
  - `write_results()`, `inspect_fmu()` 분리. CLI에 `--debug-logging` 추가.
    검증 실패 시 JSON 오류 목록을 출력. `FMICallException`도 잡아 처리.
- `src/fmpy/scenario_schema.py` (신규): 시나리오 v1 JSON Schema(draft 2020-12),
  `CAPABILITIES`(지원/미지원/한계/의미 규칙), `ERROR_CODES`(코드별 수정 방법).
- `src/fmpy/scenario_plan.py` (신규): `model_structure()`가 FMU 선언 구조를,
  `propose_scenarios()`가 돌려볼 시나리오를 도출. `archive_contents()`가
  `sources/`·`documentation/`·`resources/` 목록을 보고.
- `src/fmpy/scenario_agent.py` (신규): JSON in/out 도구 계층.
  `tools`/`schema`/`describe`/`structure`/`plan`/`review`/`context`/`validate`/`run`.
  `TOOLS`는 function-calling 형식 매니페스트. CLI는 stdout에 JSON 객체 하나만
  출력하고 종료 코드로 0 성공 / 1 불합격 / 2 문제 보고를 구분. `--scenario -`로
  표준 입력 지원.
- `src/fmpy/gui/scenario.py`: 저장을 `write_results()`로 통일(중복 제거).
  완료 상태줄에 FMU 오류·경고 건수와 조기 종료를 표시. 보고서 탭은 report를
  그대로 덤프하므로 `summary`/`rule_events`/`fmu_log`가 자동으로 보인다.
- `examples/agent/plan_and_run.py` (신규): 모델 없이 `plan` → `run` 루프를
  그대로 실행해 의존 관계·요약·부족한 도메인 정보를 출력.
- `docs/agent-integration-ko.md` (신규): 도구, 에이전트 루프, FMU에서 뽑을 수
  있는 것/없는 것, 원본 소스 코드의 한계, `plan` 제안 종류, 보고서 읽는 법,
  **CSV 입력 대비 차별점 비교 표**, 아직 없는 것.
- `docs/scenario-runner-ko.md`: 새 보고서 항목, 종료 이유, 메타데이터, 구조화된
  검증 오류, Boolean 표기 변경, `--debug-logging`, 새 문서 링크 반영.
- `pyproject.toml`: dev 의존성에 `jsonschema` 추가(스키마와 검증기 교차 확인용.
  실행기 자체는 쓰지 않는다. 현재 환경에는 nbformat 경유로 이미 설치돼 있다).
- `tests/test_scenario.py`: 구조화된 오류, 다중 오류, 라벨 유일성, 로그 수집과
  네이티브 로거 배선, 요약, Boolean 타입, 판정 여유, 규칙 발생 근거, FMU 조기
  종료 테스트 추가 (13 → 27개).
- `tests/test_scenario_agent.py` (신규, 41개): 매니페스트, 오류 봉투, describe의
  쓰기/읽기 분리, context 자급성, 다중 오류 경로, run 요약·산출물, CLI 종료 코드,
  스키마와 검증기 **교차 확인**, structure 의존 그래프, plan 제안의 유효성과
  실제 실행, plan이 판정을 만들지 않는다는 보장.

### 3번 답: CSV 입력과 시나리오 JSON의 차별점

`simulate_fmu(input=...)`(`fmpy.util.read_csv` + `fmpy.simulation.Input`)은
**자극 표**다. 모든 열을 float로 읽고(String 입력 불가), 연속 Real은 **선형 보간**,
discrete/tunable은 hold하며 시간 이벤트를 만든다. FMI 1/2/3과 ME/CS를 지원하고,
판정·조건 반응·사전 검증·조기 종료가 없다.

시나리오 JSON은 **시험 명세**다. 선언 타입대로 검증하고, 파라미터까지 초기화하며,
조건 반응(`rules`)과 합격 판정(`checks`)을 담고, 네이티브 실행 전에 전부 검증하고,
시나리오·보고서·결과를 한 폴더에 남긴다. 대신 FMI 2.0 CS 전용이고 격자가 고정이며
**보간이 없다**. 전체 비교 표는 `docs/agent-integration-ko.md`에 있다.

→ **램프 입력은 현재 CSV 경로만 가능하다.** 시나리오 형식의 가장 큰 기능적 구멍이고
실제 장비 시험에서 가장 먼저 필요해질 항목이다.

### 4번 답: FMU에서 시나리오를 뽑을 수 있는가

**선언 구조로는 된다.** `modelDescription.xml`에서 다음을 꺼내 쓴다:
ModelStructure의 **출력↔입력 의존 관계**와 `dependenciesKind`, 연속 상태·미분·
이벤트 지시자 수, `typeDefinitions`(quantity·단위·Enumeration 항목 이름과 값),
`unitDefinitions`, `logCategories`, `defaultExperiment`, CS 능력 플래그,
`variableNamingConvention`(structured면 이름 첫 구간이 하위 시스템).
FMI 2.0의 구분도 보존한다: `depends_on`이 `null`이면 속성 생략(= 모든 변수에
의존 가능), 빈 배열이면 입력에 직접 의존하지 않음(= 내부 상태에서 나옴).

**원본 소스 코드는 의도적으로 파싱하지 않는다.** FMU는 `sources/`에 C를 담을 수
있고(참조 FMU 7개, Drivetrain 122개) `structure` 도구가 목록을 보고하지만,
(a) 상용 FMU는 대개 바이너리만 담고 FMPy에 제거 기능(`remove_source_code`)도 있어
정작 중요한 FMU에서 무너진다, (b) 도구 생성 코드는 솔버 글루와 이름이 바뀐
변수뿐이어서 "무엇이어야 하는가"가 아니라 "무엇을 하는가"만 읽힌다, (c) 사용자는
자기 소프트웨어를 포팅하는 쪽이라 원본과 요구사항이 FMU 밖에 이미 있다.
특정 거동 추적에는 유효하지만 시험 계획의 출처로 삼지 않는다. 사람이 쓴 설명이
있을 수 있는 `documentation/`과 FMU가 읽는 `resources/`는 목록으로 함께 보고한다.

**판정은 뽑을 수 없다.** FMU는 자기가 무엇을 해야 하는지 말하지 않고, 선언 min/max는
유효 범위이지 합격 기준이 아니다. 그래서 `plan` 제안에는 `checks`가 **없고**
각 제안의 `needs_from_domain`에 받아올 항목을 적는다. 실행은 되지만 `passed`는
`null`이다.

`plan`이 만드는 제안: `smoke`(모든 입력 고정, 선언 출력 전부 기록),
`step-<입력>`(선언 하한→상한 계단 변경, 그 입력에 의존한다고 선언된 출력만 기록),
`dependency-coverage`(입력을 각자 다른 시점에 변경해 한 번에 모든 간선 통과),
`parameters-at-declared-bounds`(경계 선언된 파라미터를 한계값으로). 격자는
`defaultExperiment` 우선, 없으면 구간 50등분. 제안끼리 같은 격자를 공유한다.

### 5번 답: 완성된 FMU를 읽기 vs 원본 소스에서 빌드하기 (전제: 원본에 CMakeLists 있음)

전문은 `docs/agent-integration-ko.md`의 "두 경로 검토" 절에 있다. 요지:

- 전제대로 CMakeLists가 있으면 **FMPy의 FMU 생성 기능은 쓸 이유가 없다.**
  측정 결과 `fmpy.template.create_fmu`는 FMI **3.0** Float64 전용이고
  `ModelStructure`에 `dependencies`를 쓰지 않으며 `model.c`는 뼈대다.
  생성한 FMU를 `describe`에 넣으면 `supported: false`, `plan`은 `unsupported_fmu`.
- 두 경로는 배타적이지 않다. 경로 B는 사실상 "경로 A + 선언을 내가 고칠 수 있다"다.
  도구는 하나로 충분하고 체인은 `cmake --build` → `.fmu` → `review` → `plan` →
  `validate` → `run`이다.
- 그래서 `review` 도구를 추가했다(`scenario_plan.review_declaration`).
  빠진 선언을 심각도 순으로, 각 항목의 `effect`(무엇을 잃는가)와 `fix`(무엇을
  선언하면 되는가)와 함께 보고한다. 코드: `unsupported_interface`,
  `unsupported_variable_types`, `no_declared_outputs`, `undeclared_dependencies`,
  `inputs_without_declared_effect`, `missing_range`, `missing_description`,
  `missing_unit`, `enumeration_without_items`, `string_input_without_start`,
  `missing_default_experiment`, `no_log_categories`.
  확인: Feedthrough는 단위 6·범위 6·설명 14건과 `stepSize` 누락,
  Drivetrain은 `tau`가 어떤 출력에도 의존 관계로 선언되지 않은 것이 잡힌다.
  템플릿 FMU는 blocking 2건으로 분류된다.
- 합격 기준은 어느 경로로도 FMU에서 나오지 않는다. 요구사항 문서가 필요하다.
- 미결: 사용자 빌드를 호출하는 `build` 도구를 둘지. 빌드 명령과 산출물 경로가
  저장소 구조에 달려 있어 아직 만들지 않았다. 사용자 저장소 구조를 받으면 추가.

### 검증 결과

```powershell
# 소스는 이 클론, 환경은 기존 저장소의 .venv (아래 "이 세션의 실행 환경" 참고)
$env:PYTHONPATH="C:\Users\nubur\fmpy_claude\src"
C:\Users\nubur\fmu\fmpy\.venv\Scripts\python -m pytest tests/test_scenario.py tests/test_scenario_gui.py tests/test_scenario_agent.py -q
# 68 passed   (변경 전 기준선 22 passed)
C:\Users\nubur\fmu\fmpy\.venv\Scripts\python scripts/check_gui.py
# Ready: FMPy 0.3.32; MainWindow constructed successfully
```

실제 FMU로 확인한 것:

- 참조 FMU 5종(Feedthrough, BouncingBall, Stair, Drivetrain, Controller)에서
  `plan`이 만든 제안 **19건 중 19건이 검증 통과 후 실행**됨.
- `Stair.fmu`가 `stop_time` 10 s에서 doStep을 discard하며 종료를 요청하는 것을
  발견. 이전 코드는 `FMICallException`으로 중단되어 결과가 사라졌고, 지금은
  `termination: "fmu_terminated"`로 46 샘플과 합격 판정을 남긴다.
- 일부러 7개 실수를 넣은 시나리오가 **한 번의 `validate` 호출로 7개 전부**를
  경로와 함께 보고.
- `context` 출력이 약 12 KB(Feedthrough 기준)로 프롬프트에 들어간다. `context`가
  생성한 예시 시나리오가 그대로 검증·실행된다.
- 네이티브 로거 프록시를 직접 호출해 FMU 메시지가 `LogCapture`까지 도달하는 것을
  확인. 참조 FMU들은 이 시나리오에서 메시지를 내지 않아, 실제 FMU 로그 내용은
  사용자 FMU로 재확인이 필요하다.

미확인/미완료:

- upstream 전체 테스트 스위트는 실행하지 않았다(네트워크·빌드 필요). 다만 변경은
  `scenario*.py`와 `gui/scenario.py`에 한정되고 FMPy 핵심은 건드리지 않았다.
- 사용자 실제 도메인 FMU는 아직 제공되지 않았다.
- GUI 화면의 시각 확인은 하지 않았다(상태줄 문구 변경, 보고서 탭 새 항목).
- 램프 입력, 조건 유지시간/지연/복합 조건/반복, 구간·정착·도달·변화율 판정은
  여전히 미구현. 모델 호출 코드(Ollama/Qwen 연결)는 이 저장소에 없다.

### 추가 변경: 제안 선택·실행 GUI (같은 세션, 이어서)

사용자 요청: "실행을 자동화할 필요는 없고. 시나리오를 뽑아서 주면. 사용자가
나중에 선택해서 이 코드가 가상환경에서 제대로 동작하는지 확인할 수 있는
인터페이스가 필요한거야." → 질문으로 확인한 결과 **기존 PySide6 GUI를 확장**하는
쪽으로 결정.

- `src/fmpy/gui/scenario_proposals.py` (신규): `ScenarioProposalsDialog`.
  `plan()`이 만든 제안을 표(체크박스 | id | 제목 | 필요한 도메인 정보 | 결과)로
  보여준다. 기본 전체 체크, 전체 선택/해제, 행 선택 시 근거·대상·필요 정보를
  아래 패널에 표시. **선택 실행**은 체크된 것만 `ProposalRunner`(QThread)로
  순서대로 실제 FMU에 실행(기존 `ScenarioWorker`와 같은 `isInterruptionRequested`
  취소 패턴). 완료마다 결과를 저장(`<결과폴더>/<제안id>/<실행시각>/`)하고 표의
  결과 칸과 상세 패널을 갱신. `validate` 실패 제안은 기본 체크 해제 + 사유 표시.
  실행은 전부 수동 트리거이고 자동 진행·자동 적용은 없음(요청대로).
- `src/fmpy/gui/scenario.py`: `ScenarioDialog`에 **시나리오 제안 보기...** 버튼
  추가(FMU 선택 행 아래, JSON 로드 불필요). `openProposals()`가
  `ScenarioProposalsDialog`를 모달로 연다. FMU 미선택 시 상태줄 안내만.
- `tests/test_scenario_gui.py`: 제안 목록 생성·체크 상태·상세 패널
  (`test_proposals_dialog_lists_valid_checked_proposals`), 체크한 것만 실행되고
  나머지는 "대기" 그대로 유지되며 결과 파일이 실제로 쓰이는지
  (`test_proposals_dialog_runs_only_what_is_checked`), `ScenarioDialog`에서
  버튼으로 도달 가능하고 FMU 미선택 시 안내만 뜨는지
  (`test_proposals_dialog_is_reachable_from_scenario_dialog`) 확인. 4개 → 7개.
- `docs/scenario-runner-ko.md`, `docs/agent-integration-ko.md`: 사용법 반영.

검증: `pytest tests/test_scenario.py tests/test_scenario_gui.py
tests/test_scenario_agent.py -q` → **71 passed** (직전 68 → +3).
`scripts/check_gui.py`는 `ScenarioDialog`를 실제 생성하므로 새 import 체인
(`scenario.py` → `scenario_proposals.py` → `scenario_agent.py` →
`scenario_plan.py`/`scenario_schema.py`)까지 실제 창 생성으로 검증됨 —
"Ready: FMPy 0.3.32; MainWindow constructed successfully" 확인. 추가로
offscreen Qt로 직접 실행해 표 채움(7개 행), 체크 토글, 스레드 실행 완료 후
결과 칸·상세 패널·`results.csv`/`report.json` 생성을 수동으로도 확인했다.
처음에 `openProposals()`가 호출하는 `.exec()`(모달)를 헤드리스 환경에서 직접
호출해 멈춘 적이 있음 — 테스트에는 `.exec()`를 안 타고 다이얼로그를 직접
생성하거나 실제 pytest 환경(이벤트 루프 보유)에서만 돌리는 방식으로 피했다.

미완료: 소스 코드 기반(LLM이 로직을 읽고 제안) 시나리오는 아직 `plan`에
연결되지 않았다. 지금 표는 XML 선언 기반 제안만 보여준다. 나중에 그 기능을
추가하면 같은 표·같은 실행 경로를 재사용할 수 있도록 `plan()` 반환 형태
(`{"id","title","rationale","covers","needs_from_domain","scenario","valid"}`)를
그대로 따르게 만들면 된다.

### 추가 변경: 실제 서드파티 코드 기반 FMU + 로직 유래 시나리오 7개 (같은 세션, 이어서)

사용자 요청 흐름: "fmi2DoStep에서 IO를 바꿔가며 결과를 확인하는 게 되는지" →
"Ollama 연동이 어떤 식인지" → "그게 소스 코드를 읽고 시나리오를 뽑는 거 아닌지" →
"실행 자동화는 필요 없고, 제안을 뽑아서 사용자가 골라 돌려보는 인터페이스가
필요" (→ 기존 GUI 확장, 이미 위에 기록) → **"디폴트는 너(Claude)를 붙여서,
인터넷에서 실제 동작하는 C 코드를 가져와 FMU로 만들고 그걸로 5케이스 이상
시나리오를 만들어 직접 돌려보게 해달라."**

- **가져온 코드**: [`pms67/PID`](https://github.com/pms67/PID) (c) 2020 Philip
  Salmony, **MIT 라이선스**. `PID.c`/`PID.h` 원본 그대로(한 글자도 수정 안 함),
  비례+사다리꼴 적분(anti-windup 클램프)+대역제한 미분(derivative-on-
  measurement)을 구현하는 70줄짜리 실제 컨트롤러.
- `examples/agent/pid_demo/src/pid_fmi2.c` (신규, 직접 작성): FMI 2.0
  Co-Simulation 래퍼. `FMU2Slave`가 `inspect.getmembers`로 로드를 시도하는
  모든 `fmi2*` 심볼(33개, ME 전용 10개는 제외)을 `objdump`로 대조해 정확히
  일치시켰다 — 누락 1개라도 있으면 `FMU2Slave()` 생성 자체가 실패함을
  사전에 코드 추적으로 확인(`inspect.getmembers(self)` → `getattr(self.dll, name)`).
  `fmi2ExitInitializationMode`에서 `limMin<=limMax`, `limMinInt<=limMaxInt`를
  검사해 위반 시 `fmi2Error`+로그 메시지로 거부(진짜 FMI 호출 실패 경로 시연).
  `fmi2DoStep`에서 `communicationStepSize`와 파라미터 `T`의 불일치를 FMU
  자신의 로거로 경고(`LogCapture` 기능을 실제 내용으로 시연). 초기화 시 한 번
  `PIDController_Update`를 호출해 t=0 샘플이 "calculated"로 의미 있게 나오게 함.
- `examples/agent/pid_demo/modelDescription.xml` (신규, 직접 작성): 변수 16개,
  출력 4개(`output`,`integrator_state`,`differentiator_state`,`error`) 전부에
  **정확한** `ModelStructure` 의존 관계를 선언(`differentiator_state`는
  `setpoint`에 의존하지 않음 — 코드의 "derivative on measurement" 성질을
  그대로 선언에 반영). `validate_model_description(..., validate_model_structure=True)`
  결과 **문제 0건**.
- 빌드: `C:\msys64\mingw64\bin\gcc.exe`(mingw-w64 15.2.0)로 컴파일 성공(경고 0).
  `examples/agent/pid_demo/build.sh` (신규)로 재현 가능. `*.fmu`/`*.dll`은
  저장소 전체 관례대로 git에 커밋하지 않음(`.gitignore`) — 이 PC에는 빌드된
  `PID.fmu`가 바로 사용 가능한 상태로 존재.
- `examples/agent/pid_demo/scenarios/01~07_*.json` (신규): **코드를 직접 읽고**
  도출한 7개 시나리오(요청한 "5케이스 이상" 충족). 각각 실제로 실행해 숫자를
  확인한 뒤 JSON에 반영했다(추측 없음):
  1. `step_response`: 비례+적분 성장, 포화 전(output 0→7.19)
  2. `measurement_step_derivative_kick`: 미분 킥(differentiator_state 최대 8.0)
     후 ~tau 내 감쇠
  3. `anti_windup_clamp`: integrator_state가 정확히 5.0(limMaxInt)에서 멈춤
  4. `output_saturation`: output은 즉시 10.0 포화, integrator_state는 아직
     3.05(두 클램프가 다른 시점에 걸림을 보여줌)
  5. `setpoint_step_no_derivative_kick`: differentiator_state가 전 구간
     정확히 0 — "derivative는 setpoint에 반응 안 함"을 수치로 증명(02와 대조)
  6. `communication_step_mismatch_warns`: T≠step_size일 때 10스텝 모두 경고
     로그 10건 캡처(`fmu_log`/`log_warnings` 실제 내용으로 시연)
  7. `invalid_integrator_bounds_fails_init`: **의도적으로 실패하는 케이스**.
     `limMinInt>limMaxInt` → `run()`이 `ok:false`/`fmi_call_failed` 반환을
     그대로 확인(이전에 만든 오류 처리 경로의 실증).
  `plan()`(XML만 보는 자동 제안)은 이 중 어느 것도 못 뽑는다는 것도 확인 —
  5개 제안(`smoke`,`step-setpoint`,`step-measurement`,`dependency-coverage`,
  `parameters-at-declared-bounds`)은 전부 돌아가지만 anti-windup·미분 킥·
  초기화 실패는 XML 선언만으로는 도달 불가능함을 데이터로 보여준다.
- `review()`로 이 FMU 자체도 점검: **blocking 0건**, medium 2건(단위·범위
  미선언)뿐. 같은 도구로 본 Feedthrough는 missing_description 14건 등
  더 많은 결함 — "선언을 풍부하게 쓰면 review 결과가 좋아진다"는 주장을
  두 FMU 비교로 실증.
- `tests/test_pid_demo.py` (신규, 9개): 7개 시나리오 전부의 **정확한 수치**를
  고정(예: `integrator_state max == pytest.approx(5.0)`,
  `differentiator_state min==max==0`, 시나리오 7은 `fmi_call_failed` 확인).
  `review()`/`plan()` 결과도 고정. `PID.fmu`가 없으면 스킵(`pytest.mark.skipif`).
- `examples/agent/pid_demo/README.md` (신규): 출처·라이선스, 빌드법, 7개 표
  (겨냥한 것/코드의 어디를 읽었는지/실제 확인된 결과), GUI·CLI로 직접 돌려보는
  법, "이게 왜 에이전트가 코드를 읽고 만든 시나리오인가" 설명.

검증: `pytest tests/test_pid_demo.py -q` → **9 passed**. 전체
`pytest tests/ -q`(네트워크 불필요 범위, dask 미설치로 `test_examples.py`
제외)는 **129 passed, 8 failed, 3 skipped** — 8개 실패는 모두
`native/thirdparty/Reference-FMUs` 서브모듈 미체크아웃, 설치된 CLI
엔트리포인트 부재, cmake subprocess 등 **이 세션 이전부터 있던 환경 간극**이며
scenario 관련 코드와는 무관함을 각 트레이스백으로 확인(시나리오/GUI 관련
코드는 한 줄도 건드리지 않음).

**중요한 한계**: 사용자가 요청한 "디폴트는 너(Claude)를 붙여서"는 **이 세션
안에서 제가 직접 코드를 읽고 시나리오를 작성하는 방식으로 수행**했다 —
API로 Claude를 호출하는 자동화 스크립트를 새로 연결한 것은 **아니다**
(`ANTHROPIC_API_KEY`가 이 환경에 없어 확인 불가했고, 묻지 않고 바로 이 방식을
선택했음을 사용자에게 명시해야 함). `fmpy.scenario_agent`의 도구 계층
자체는 이전 세션에서 이미 모델-무관(model-agnostic)하게 만들어져 있으므로,
나중에 실제 API 루프(Ollama든 Claude API든)를 연결해도 같은
`context→validate→run` 흐름을 그대로 재사용할 수 있다. 그 자동화 스크립트
자체는 아직 작성하지 않았다.

### 추가 변경: 실제 에이전트 연동 코드 (같은 세션, 이어서) + 커밋/푸시

사용자 요청: "커밋 푸쉬 진행하고 연동까지 진행해줘."

- **커밋/푸시**: `main`은 보호 관례상 직접 커밋하지 않고
  `feature/scenario-agent-integration` 브랜치를 새로 만들어 커밋 후
  `origin`에 푸시했다(`5fc84f9`). PR은 아직 안 만들었다 — 필요하면 요청.
- **연동**: 이 PC에 **Ollama 0.40.4가 실제로 돌고 있고 `gemma:2b`가 설치되어
  있음**을 확인(`curl localhost:11434/api/tags`). `ANTHROPIC_API_KEY`와
  `anthropic` 패키지는 이 환경에 없음(이전에도 확인, 재확인).
  - `examples/agent/ollama_loop.py` (신규): `context`→모델 호출→`validate`
    교정 루프→`run`까지 도는 실제 코드. **PID.fmu에 대해 실제로 2회 실행해
    확인**: 루프 자체(파싱→검증→교정→실행)는 둘 다 1회 시도로 통과. 단
    `gemma:2b`(2B, completion-only, tool-calling 미지원)는 자연어 요청에서
    숫자 하나(setpoint 값)만 반영하고 `outputs`/`checks` 같은 구조적 지시는
    못 따르며 예시 JSON을 거의 그대로 돌려줬다 — 솔직하게 기록.
  - `examples/agent/claude_loop.py` (신규): 같은 루프를 Anthropic Messages
    API 네이티브 tool-calling(`scenario_agent.TOOLS`를 `input_schema`로 전달)
    으로 구현. **이 환경엔 SDK/키가 없어 실제 API 호출은 검증 못 했다** —
    `py_compile` 통과, `--help` 동작, 의존성 누락 시 종료 코드 2로 깨끗하게
    실패하는 것까지만 확인. 사용자 쪽에서 키로 첫 실행을 확인해야 한다.
  - `docs/agent-integration-ko.md`에 두 스크립트 사용법과 위 실측 결과를
    "실제로 연결해 돌려본 결과 / 아직 검증 안 한 것"으로 솔직하게 구분해
    기록했다.
- 검증: 소스 코드(`src/fmpy/scenario*.py`, `gui/scenario*.py`)는 이번에
  건드리지 않았으므로 `pytest tests/test_scenario.py tests/test_scenario_gui.py
  tests/test_scenario_agent.py tests/test_pid_demo.py -q` 재확인 →
  **80 passed**(기존 71 + pid_demo 9).

미완료: `qwen2.5` 같은 tool-calling 지원 모델로 Ollama 쪽을 다시 시도하면
`gemma:2b`보다 나을 가능성이 높으나 실측하지 않았다(이 PC엔 `gemma:2b`만
pull되어 있음). Claude API 루프는 사용자 환경에서 최초 1회 실행 확인이
필요하다. PR 생성 여부는 사용자 확인 대기.

### 추가 변경: GUI 챗봇 인터페이스 (같은 세션, 이어서)

사용자 요청: "fmpy 실행할때 에이전트 연동해서 챗봇형태로 자연어 요청하는
인터페이스 추가해줘."

- **중복 제거 먼저**: `examples/agent/ollama_loop.py`/`claude_loop.py`에
  있던 루프 로직(prompt 템플릿, `ollama_generate`, `generate_scenario`,
  Claude tool-calling 루프)을 `src/fmpy/scenario_backends.py`(신규, 진짜
  라이브러리 코드)로 옮겼다. 두 CLI 스크립트는 이제 그 모듈을 부르는 얇은
  래퍼로 교체했고, **리팩터 후 다시 실행해 동작이 그대로인지 확인**했다
  (Ollama는 실제 재실행, Claude는 `py_compile`+ImportError 경로 재확인).
  `on_log`/`cancelled` 콜백을 추가해 GUI의 QThread에서도 그대로 재사용
  가능하게 했고, `include_samples`를 통과시켜 GUI가 전체 샘플 표를 받아
  그래프를 그릴 수 있게 했다(`run()`은 기본적으로 샘플을 안 돌려주므로).
  `ollama_models(host)`도 추가(서버에 pull된 모델 목록, 끊겨 있으면 `[]`).
- `src/fmpy/gui/agent_chat.py` (신규): `AgentChatDialog`. FMU 선택 +
  백엔드(Ollama/Claude) 선택 + 모델 선택(Ollama는 "모델 목록 새로고침"으로
  서버에서 직접 조회) + 결과 폴더 + 채팅 입력창 + 결과 그래프 탭(기존
  `ScenarioResults` 재사용). `AgentChatWorker`(QThread)가
  `scenario_backends.run_with_ollama/run_with_claude`를 호출. 매 턴마다
  `<결과폴더>/turn-NNN-HHMMSS/`에 결과 저장. "시나리오 JSON 저장...",
  "결과 폴더 열기" 버튼 제공. **모델은 FMU를 직접 건드리지 않음** —
  검증·실행은 항상 `scenario_agent`를 통해서만.
- 진입점 두 곳에 연결: `src/fmpy/gui/scenario.py`의 `ScenarioDialog`에
  "에이전트에게 요청..." 버튼 추가(제안 보기 버튼 옆), `src/fmpy/gui/
  MainWindow.py` 툴바에도 같은 이름의 액션 추가(시나리오 적용 없이도,
  FMU를 열자마자 바로 접근 가능 — "fmpy 실행할 때" 요청에 맞춤).
- `tests/test_agent_chat.py` (신규, 7개): 구성·백엔드 전환(Claude 선택 시
  host 비활성화)·FMU/요청 없을 때 거부·시나리오 저장(모킹)·MainWindow 툴바
  연동은 네트워크 없이 확인. **Ollama가 실제로 떠 있을 때만** 도는 2개는
  `ollama_models()`로 skip 조건을 걸고, 실제 FMU에 대해 턴 전체(생성→검증→
  실행→저장)와 중지(취소) 경로를 **진짜로 실행**해 확인했다(모킹 없음).
- 발견한 버그 아님: `.exec()`를 헤드리스(offscreen) 환경에서 직접 호출하면
  모달이라 이벤트 루프가 없는 한 멈춘다 — 테스트와 수동 확인 모두 다이얼로그를
  직접 생성해 `.exec()`를 피하는 방식으로 작성했다(기존 `ScenarioProposalsDialog`
  테스트와 같은 패턴). QThread의 내장 `finished` 시그널은 큐에 걸려 전달되므로
  취소 테스트에서 `worker.wait()` 뒤 `app.processEvents()`를 한 번 더 호출해야
  `setBusy(False)`가 실행된 상태를 확인할 수 있었다(테스트 쪽 수정, 다이얼로그
  버그 아님).
- `docs/scenario-runner-ko.md`, `docs/agent-integration-ko.md`에 사용법 반영.

검증: `pytest tests/test_scenario.py tests/test_scenario_gui.py
tests/test_scenario_agent.py tests/test_pid_demo.py tests/test_agent_chat.py -q`
→ **87 passed**(기존 80 + 신규 7). `scripts/check_gui.py`는 `ScenarioDialog`를
실제 생성하므로 `agent_chat.py` import 체인까지 실제 창 생성으로 검증됨.

미완료: 이번 턴에서는 **커밋/푸시하지 않았다** — 사용자가 명시적으로 요청하면
진행. `qwen2.5` 등 tool-calling 지원 모델로 Ollama 쪽 품질 재확인, Claude
백엔드의 실제 API 첫 실행은 여전히 미확인(환경에 키 없음).

### 추가 변경: 여러 시나리오 일괄 실행 + 판정 종합 (같은 세션, 이어서)

사용자 요청: "시나리오를 여러개 돌릴 수 있고 판정결과를 정리해주는 역할까지."

- `src/fmpy/scenario_agent.py`에 `run_many(fmu, scenario_paths, output=None,
  include_samples=0, debug_logging=False, on_progress=None, cancelled=None)`
  추가. 파일을 하나씩 읽어 `run()`으로 실행하고, 하나가 깨져도(JSON 아님/
  검증 실패/FMI 호출 거부) 나머지는 계속 진행. `_summarize_batch()`로
  합격/불합격/판정 기준 없음/실행 불가 개수를 집계하고,
  `format_batch_report()`로 사람이 읽는 Markdown 한 줄 표를 만든다.
  `output`을 주면 `<폴더>/<파일이름(stem 또는 시나리오 name)>/`마다 개별
  `report.json`/`results.csv`, 최상위에 `summary.json`을 쓴다.
  `on_progress(index, entry)`로 진행 중 콜백, `cancelled()`로 중지(남은
  파일은 읽지도 않고 `cancelled` 오류로 건너뜀 — 실제로 확인: 취소 시
  존재하지 않는 다음 파일도 읽기 전에 건너뛰는 것까지 검증).
  `TOOLS`/`_DISPATCH`에 `"batch"` 등록, CLI `batch` 서브커맨드 추가
  (`--scenarios`는 파일 여러 개 또는 디렉터리 — 디렉터리면 `*.json` 자동
  수집, `--report`로 Markdown 출력, 종료 코드 0/1/2는 불합격/실행 불가
  유무로 구분).
- **실제 PID 데모 7개로 검증**: `run_many`가 `5 합격 · 0 불합격 · 1 판정
  기준 없음(02) · 1 실행 불가(07, 의도된 실패)`를 정확히 집계. CLI
  `batch --report`로도 동일하게 확인, `summary.json`과 6개 서브폴더(07은
  실행 자체가 안 돼 폴더 없음)까지 디스크에서 확인.
- `src/fmpy/gui/scenario_batch.py` (신규): `ScenarioBatchDialog`. "파일
  추가.../폴더에서 추가..." 로 시나리오 목록 구성(중복 제거), 체크박스로
  선택, `BatchRunner`(QThread)가 `run_many`를 그대로 호출해 백그라운드
  실행, 행마다 실시간 상태 갱신, 완료 시 요약 라벨(예: "7개 중 합격 5 ·
  불합격 0 · 판정 기준 없음 1 · 실행 불가 1")과 "보고서 저장..." 버튼
  활성화. 행 선택 시 실패한 검사·FMU 로그 건수·저장 위치를 상세 패널에
  표시. `ScenarioProposalsDialog`와 역할이 다름(그건 `plan()`이 XML만
  보고 만든 판정 기준 없는 제안용, 이건 **사용자가 이미 쓴 판정 기준 있는**
  시나리오 묶음용).
- 진입점 두 곳: `ScenarioDialog`에 "여러 시나리오 실행..." 버튼(제안 보기/
  에이전트 요청 버튼 옆), `MainWindow` 툴바에도 같은 액션(FMU 열자마자
  접근 가능).
- `tests/test_scenario_agent.py`에 `run_many`/`format_batch_report`/CLI
  `batch` 테스트 8개 추가(합격·불합격·판정없음·실행불가 혼합, 출력 폴더
  구조, 취소, FMU 자체가 안 읽히는 경우, 디렉터리 확장) → 43개.
  `tests/test_pid_demo.py`에 실제 PID 7개로 집계·폴더 구조 고정 테스트
  2개 추가 → 11개. `tests/test_scenario_batch_gui.py` (신규, 8개): 목록
  구성·중복 제거·제거/초기화, FMU 없을 때 거부, **실제 FMU로 4가지 결과
  섞어서** 집계·상세 패널·폴더 구조 확인, 선택 실행만 적용, 보고서 저장,
  취소, 두 진입점 연결까지 검증.
- `docs/scenario-runner-ko.md`, `docs/agent-integration-ko.md`에 사용법과
  실측 결과 반영.

검증: `pytest tests/test_scenario.py tests/test_scenario_gui.py
tests/test_scenario_agent.py tests/test_pid_demo.py tests/test_agent_chat.py
tests/test_scenario_batch_gui.py -q` → **104 passed**(기존 87 + 신규 17).
`scripts/check_gui.py`로 `scenario_batch.py` import 체인까지 실제 창 생성
검증.

미완료: 이번에도 **커밋/푸시는 안 했다** — 요청 시 진행.

### 추가 변경: 에이전트 요청/배치 실행을 시나리오 창에 통합 + 에이전트 설정 GUI (같은 세션, 이어서)

사용자 요청: "여러시나리오 요청을 기존 scenario 에 통합해줘. 에이전트 요청도
마찬가지." 그 다음 "그리고 ai 에이전트 설정을 gui에서 할 수 있게해줘."

- **통합**: `AgentChatDialog`/`ScenarioBatchDialog`를 별도 팝업(QDialog)에서
  `ScenarioDialog`가 직접 품는 탭(QWidget)으로 바꿨다. 클래스 이름은 혼란을
  줄이려고(기존 테스트가 이 이름으로 많이 참조) 그대로 두고 **베이스 클래스만
  QDialog→QWidget으로 교체**, `reject()`는 QWidget에 없는 메서드라 제거하고
  `isBusy()` 헬퍼를 추가해 부모가 "이 탭이 지금 돌고 있는가"를 물어볼 수 있게
  했다. `ScenarioDialog`에 탭 2개 추가(`에이전트에게 요청`, `여러 시나리오
  실행`, 총 5탭), 기존 "에이전트에게 요청.../여러 시나리오 실행..." 버튼은
  제거(제안 보기 버튼은 요청에 없어 그대로 둠). FMU는 `ScenarioDialog.fmuPath`
  의 `textChanged` 시그널에 `syncFmu()`를 연결해 **누가 바꿔도**(내부
  버튼이든 MainWindow가 직접 `.setText()`하든) 두 탭에 자동 전파.
  `ScenarioDialog.reject()`/`closeEvent()`가 자기 워커뿐 아니라 두 탭의
  `isBusy()`도 확인하도록 확장(돌고 있으면 취소 요청하고 닫기 거부) —
  하나의 탭이 실행 중인데 창을 닫으면 QThread가 고아로 남는 위험을 막음.
  `MainWindow`는 `ensureScenarioDialog()`(기존 `openScenario`의 생성/재사용
  로직을 분리) → `showAgentChatTab()`/`showBatchTab()`으로 전환 후 `.exec()`
  하도록 바꿔, 툴바 버튼은 그대로 두되 전부 **같은 통합 창**을 열게 했다.
  실제로 임베드된 탭에서 배치를 돌려 확인(요약 라벨 정상 집계).
- **에이전트 설정**: `src/fmpy/gui/agent_settings.py` (신규).
  `load()`/`save()`가 `QSettings(QSettings.IniFormat, QSettings.UserScope,
  "fmpy", "scenario_agent")`로 **레지스트리가 아니라 INI 파일**에 저장(경로
  투명성을 위해 의도적으로 IniFormat 선택). `AgentSettingsDialog`:
  기본 백엔드, Ollama Host/기본 모델/최대 재시도 횟수, Claude 기본 모델/
  API 키(선택, `QLineEdit.Password` 에코, 평문 저장됨을 툴팁에 명시)/최대
  턴 수, 기본 결과 폴더. `AgentChatDialog`에 "설정..." 버튼 추가 → 저장 시
  `applySettings()`가 백엔드/호스트/모델/출력 폴더를 즉시 반영하고 설정을
  `send()`가 쓰는 kwargs(`max_attempts`, `max_turns`, `api_key`)에 꽂음.
  `scenario_backends.run_with_claude`에 `api_key=None` 파라미터 추가 —
  주어지면 `anthropic.Anthropic(api_key=...)`, 없으면 기존처럼 환경 변수
  (CLI와 동일 동작, 하위 호환 깨지지 않음).
- `tests/test_agent_settings.py` (신규, 7개): load 기본값, save가 지정한
  키만 덮어씀, 타입 보존(int), 실제 INI 파일 생성 확인, 다이얼로그가 저장된
  값을 정확히 반영, 저장 시 `saved` 시그널+영속화+Accepted 확인. 모든 테스트
  `QSettings`를 `tmp_path` 기반 임시 파일로 monkeypatch해 **실제 사용자
  설정 파일을 절대 건드리지 않음**(세션 간 오염 방지).
- `tests/test_agent_chat.py`에 동일한 격리 픽스처(autouse) 추가 + 설정
  연동 테스트 4개(설정 저장→채팅창 반영→재생성 시에도 유지, 취소 시
  미저장, 실제 `send()` 호출에 `api_key`/`max_turns`가 전달되는지 워커를
  가짜로 바꿔 확인) → 11개.
- `tests/test_scenario_batch_gui.py`의 낡은 테스트(버튼 존재 확인)를 탭
  임베드+FMU 동기화 확인으로 교체. `tests/test_scenario_gui.py`는 변경 없이
  전부 재통과(하위 호환 확인).

검증: `pytest tests/test_scenario.py tests/test_scenario_gui.py
tests/test_scenario_agent.py tests/test_pid_demo.py tests/test_agent_chat.py
tests/test_scenario_batch_gui.py tests/test_agent_settings.py -q` →
**114 passed**(기존 104 + 신규 10). `scripts/check_gui.py`로 실제
`MainWindow`/`ScenarioDialog`(5탭 전부 포함) 생성 확인.

미완료: 메인 툴바에 별도 "에이전트 설정..." 버튼은 추가하지 않았다 — 채팅
탭 안의 "설정..." 버튼으로 충분하다고 판단(요청 범위를 벗어난 중복 UI
방지). 이번에도 **커밋/푸시는 안 했다** — 요청 시 진행.

### 추가 변경: 메인 툴바를 "Scenario..." 하나로 통합 (같은 세션, 이어서)

사용자 지적: "아직 gui에 scenario 에이전트에게 요청 여러 시나리오 실행 3개가
보이는데 왜그런거야?" → "시나리오창 하나에 통합해줘. 그리고 agent 설정은
어디서 하는지."

원인: 탭 통합은 실제로 되어 있었지만(앞 커밋), 메인 툴바에는 `Scenario...`,
`에이전트에게 요청...`, `여러 시나리오 실행...` **세 버튼을 그대로 남겨둔
채** 셋 다 같은 창을 열도록만 바꿔서, 안은 통합됐는데 밖(툴바)은 여전히
3개로 보였다. 지적이 맞아서 그대로 수정:

- `src/fmpy/gui/MainWindow.py`에서 `agentChatAction`/`batchAction` 툴바
  추가와 `openAgentChat()`/`openBatch()` 메서드를 제거. 툴바에는 이제
  시나리오 관련 버튼이 **`Scenario...` 하나**(+ `시나리오 해제`)뿐이고,
  툴팁에 "시나리오 로드·실행, 에이전트에게 요청, 여러 시나리오 실행을
  모두 여기서 합니다"를 적어 안내. `ensureScenarioDialog()`/
  `showAgentChatTab()`/`showBatchTab()`는 그대로 유지(테스트와 내부 탐색에
  계속 사용).
- 실제 창에서 `w.ui.toolBar.actions()`로 툴바 구성을 확인: 시나리오 관련은
  `['Scenario...', '시나리오 해제']` 뿐이고, 그 창의 탭은 여전히 5개
  (`['시나리오', '결과 그래프', '판정 / 결과', '에이전트에게 요청', '여러
  시나리오 실행']`) 전부 있음을 실제로 띄워서 확인.
- `tests/test_agent_chat.py`/`tests/test_scenario_batch_gui.py`의 낡은
  "툴바 액션이 있다" 단언을 "툴바 액션이 **없다**(`not hasattr`)" +
  "`ensureScenarioDialog()`로 열어서 탭 전환은 여전히 된다"로 교체.
- **에이전트 설정 위치**(사용자 질문에 대한 답): 메인 툴바 `Scenario...`
  → 열린 창의 **"에이전트에게 요청"** 탭 → 그 탭 안의 **"설정..."** 버튼.
  별도 메인 툴바 버튼은 두지 않았다(이미 지난 라운드에서 그렇게 결정했고
  이번에도 유지) — 사용자에게 직접 이 경로를 안내함.

검증: 전체 재실행 → **114 passed**(개수 동일, 테스트 내용만 갱신).
`scripts/check_gui.py` + 직접 띄워서 툴바·탭 구성을 실제로 확인.
`feature/agent-chat-and-batch` 브랜치에 커밋 후 푸시.

### 추가 변경: 시나리오 창 최대화, 에이전트 탭 사이드바형 채팅 (같은 세션, 이어서)

사용자 요청: "시나리오 실행창을 최대화 할 수 있게 변경해주고 에이전트
요청은 사이드바 형태로 채팅하는 느낌으로."

- `src/fmpy/gui/scenario.py`: `ScenarioDialog.__init__`에서
  `self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint |
  Qt.WindowMinimizeButtonHint)` 추가. QDialog는 기본적으로 최대화 버튼이
  없어서(최소화도 없음) 명시적으로 플래그를 더해야 한다. 실제 창에서
  `windowFlags()`로 두 플래그가 모두 켜졌는지 확인.
- `src/fmpy/gui/agent_chat.py`: 내부에 있던 "대화"/"결과 그래프" `QTabWidget`
  전환 방식을 없애고 **`QSplitter`로 좌우 분할**했다. 왼쪽
  (`_buildChatSidebar()`, 폭 280~480px 제한)은 대화 내역(`transcript`)과
  입력창(`input`)+보내기/중지 버튼만 있는 전형적인 채팅 사이드바. 오른쪽
  (`_buildMainArea()`)은 FMU/백엔드/모델/결과 폴더 설정 + 결과 그래프
  (`plot`)를 전부 모아 항상 보이게 했다 — 턴이 끝나면 바로 오른쪽 그래프가
  갱신되고 탭을 전환할 필요가 없다. 위젯 속성 이름(`fmuPath`, `backendCombo`,
  `transcript`, `input`, `plot` 등)은 기존 테스트가 많이 참조해서 전부
  그대로 유지하고 **컨테이너 구조만** 바꿨다. `onResult()`의
  `self.tabs.setCurrentWidget(self.plot)` 호출은 더 이상 `self.tabs`가 없어
  제거(그래프가 항상 보이므로 전환이 필요 없음).
- 실제로 end-to-end 턴을 새 레이아웃으로 돌려 확인: 사이드바에 요청 입력 →
  오른쪽 그래프에 결과 데이터 1건 반영, 대화 내역에 텍스트 반영.

검증: 전체 재실행 → **114 passed**(위젯 속성이 그대로라 테스트 변경 없이
통과). `scripts/check_gui.py` + `windowFlags()`/탭 구성 실제 확인.

### 추가 변경: 에이전트를 둘로 분리 — 실행·분석 에이전트 신규 구현 (같은 세션, 이어서)

사용자 요청: "에이전트를 두개로 나누자 — 시나리오 생성 에이전트 / 시나리오
실행 및 분석 에이전트. [후자는] 기존에 있던 시나리오를 바탕으로 자동으로
실행하고 결과를 정리해주는 역할. 지금은 이 에이전트에 포커스를 맞춰서
진행해주고. 폴더경로를 주면 이 에이전트가 폴더 목록에 있는 모든 시나리오를
체크해서 돌려보고 결과를 판단할 수 있는 역할."

**생성 에이전트**(`AgentChatDialog`, `generate_with_ollama`/`run_with_claude`)
는 건드리지 않았다. **실행·분석 에이전트**를 새로 만들었다:

- `src/fmpy/scenario_backends.py`:
  - `_ollama_generate`에 `json_mode` 파라미터 추가(기본 True=기존 생성용,
    False=분석용 — `format: "json"`을 안 강제해서 자연어 프로세 응답을 받음).
  - `ANALYSIS_PROMPT_TEMPLATE`/`_analysis_prompt()`: `format_batch_report()`가
    쓰는 것과 **똑같은 데이터**(요약 숫자 + 시나리오별 합격/불합격/비고)만
    모델에게 보여주고 "표에 없는 내용을 지어내지 마라"고 명시. 샘플 표는
    아예 전달 안 함.
  - `analyze_results_with_ollama`/`analyze_results_with_claude`: **이미 계산된**
    `summary`/`results`를 받아 모델에게 설명만 요청(재실행 없음). GUI의
    "분석 요청" 버튼이 쓰는 핵심 함수 — 배치를 두 번 돌리지 않으려고 실행과
    분석을 분리했다.
  - `analyze_with_ollama`/`analyze_with_claude`: 폴더 경로를 받아 **(1)**
    `run_many`로 전부 실행 **(2)** 위 함수로 분석, 둘을 합쳐 CLI가 쓰는
    원샷 함수. 배치 자체가 실패하면(FMU 못 읽음 등) 분석을 시도하지 않고
    그대로 반환. 모델이 응답 못 해도(`RuntimeError`) 실행 결과는 보존되고
    `analysis: null` + `analysis_error`만 붙는다 — **실행 성공과 분석 성공을
    분리**(기존 "실행 성공 ≠ 시험 합격" 원칙과 같은 결).
- `examples/agent/analyze_scenarios.py` (신규): `--fmu`/`--folder`/
  `--backend {ollama,claude}`/`--model`/`--output`. 종료 코드: 0 전부
  정상, 1 불합격 있음, 2 실행 불가 있음 또는 폴더에 `.json` 없음.
- `src/fmpy/gui/scenario_batch.py`("여러 시나리오 실행" 탭)에 **"분석
  에이전트"** 영역 추가: 백엔드/모델 선택 + "분석 요청" 버튼(배치 실행
  전엔 비활성) + 읽기 전용 결과 텍스트. `AnalysisRunner`(QThread)가
  `analyze_results_with_*`를 호출 — **`run_many`를 다시 안 부른다.**
  실제로 스파이를 꽂아 `scenario_agent.run`이 분석 요청 중 **0번** 호출되는
  것을 테스트로 확인(재실행 안 함을 증명). `isBusy()`/`closeEvent()`가
  배치 워커와 분석 워커 둘 다 확인하도록 확장.
- **실제로 PID 데모 7개 폴더로 끝까지 돌려 확인**: 실행·집계는
  `run_many`를 직접 부른 것과 **100% 동일**(`5/0/1/1`). 모델(`gemma:2b`)의
  자연어 설명 자체는 **표를 잘못 읽었다** — "7개 모두 합격"이라고 주장하거나
  합격한 시나리오를 판정 기준 없음으로 잘못 분류. 파이프라인은 정확하고
  약한 모델의 표 읽기 신뢰도만 낮다는 걸 솔직하게 기록(생성 에이전트에서
  본 것과 같은 종류의 한계). 모델 응답 없이 실행만 보존되는 것(불통 호스트,
  취소)도 실제로 확인.
- `tests/test_scenario_backends.py` (신규, 11개): 프롬프트 포맷팅(순수
  함수, 네트워크 불필요), 빈 폴더 거부(두 백엔드 모두, Claude는 패키지
  없이도 이 경로는 통과), `anthropic` 미설치 시 `ImportError` 전파(패키지가
  실제로 없을 때만 돎), 모델 불통 시 실행 결과 보존 확인, 취소 시 거동,
  **Ollama 실제 서버로** 분석 단독 호출/폴더 전체 파이프라인/PID 데모 정확한
  집계(모델 응답 내용 자체는 신뢰 안 하고 집계 숫자만 단언 — 주석에 사유 명시).
- `tests/test_scenario_batch_gui.py`에 3개 추가: 배치 실행 전 분석 버튼
  비활성, 실제 배치 실행 후 분석이 **재실행 없이** 결과를 설명(스파이로
  `scenario_agent.run` 호출 횟수 0 확인), 백엔드 전환 시 기본 모델명.
  `isolated_agent_settings` 픽스처도 이 파일에 추가(이제
  `ScenarioBatchDialog`도 `agent_settings`를 읽음).
- `docs/agent-integration-ko.md`에 "두 개의 에이전트" 비교 표와 "분석
  에이전트" 절 추가, `docs/scenario-runner-ko.md`에 사용법 한 단락 추가.

검증: `pytest tests/test_scenario.py tests/test_scenario_gui.py
tests/test_scenario_agent.py tests/test_pid_demo.py tests/test_agent_chat.py
tests/test_scenario_batch_gui.py tests/test_agent_settings.py
tests/test_scenario_backends.py -q` → **128 passed**(기존 114 + 신규 14,
Ollama 실제 호출이 많아 전체 실행에 약 2분 30초 소요). `scripts/check_gui.py`
+ 실제 GUI로 배치 실행→분석 요청까지 end-to-end 수동 확인.

미완료: 이번에도 **커밋/푸시는 안 했다** — 요청 시 진행. CLI 스크립트
(`analyze_scenarios.py`)는 Claude 백엔드를 실제 API로 검증하지 못함(이
환경에 키/패키지 없음, 기존과 동일한 제약).

### 추가 변경: 시나리오 창에 다중 선택 목록 통합, "여러 시나리오 실행" 탭 제거 (같은 세션, 이어서)

사용자 요청: "지금 보면 시나리오를 하나 선택하는 구성이 기본인데 여러 JSON을
선택할 수 있도록 해줘 그리고 여러 시나리오 실행 탭은 없애줘." 이어서(같은
턴 중) "시나리오 창에 왼쪽에 트리로 보이도록하던지. 추가한 시나리오목록을
볼 수 있게."

**`src/fmpy/gui/scenario.py`를 다시 작성했다.** 핵심 설계 원칙: 기존
단일 시나리오 흐름(`loadScenario(path)`가 파싱→미리보기→검증→메인 재생
자동 적용까지 하던 동작, `validate()`/`startRun()`/`applyToMain()`과
`scenarioApplied`/`scenarioCleared`/`resultReady` 신호)은 **한 글자도
바꾸지 않았다** — `MainWindow`가 이름으로 직접 호출하고(`fmuPath`,
`validate()`, `startRun()`) 기존 테스트가 이 동작을 정확히 검증하고
있었기 때문. 그 위에 새 기능을 **추가**하는 방식으로 설계했다:

- `QSplitter`로 창을 좌/우로 나눴다. 왼쪽은 새 **시나리오 목록** 사이드바
  (`QTableWidget`, 체크박스 있는 행), 오른쪽은 기존과 같은 `QTabWidget`
  (탭 4개로 줄었다 — 아래 참고).
- **시나리오 로드...**는 `QFileDialog.getOpenFileNames`(복수 선택)로
  바뀌었다. 여러 파일을 고르면 전부 목록에 추가되고(경로 기준 중복 제거),
  새로 추가된 첫 파일이 자동으로 강조(select)된다 — 그 강조가
  `onFileSelectionChanged()`를 거쳐 **기존 `loadScenario()`를 그대로**
  호출한다. 즉 "목록에서 행 하나를 클릭 = 예전의 시나리오 로드"와 완전히
  같은 동작(미리보기 갱신 + 검증 + 메인 재생 자동 적용 + 시나리오 탭으로
  전환)이라서, 단일 파일만 쓰던 기존 테스트의 기대와 충돌하지 않는다.
  **폴더에서 추가...**로 폴더의 `*.json`을 한꺼번에 넣을 수도 있다.
- 체크박스는 강조(선택)와 **별개**다 — "시나리오 실행"(기존 버튼, 기존
  의미 그대로: 지금 강조된 **하나**를 실행)과 새 **"선택 항목 실행"**
  (체크된 **여러 개**를 실행)을 서로 다른 버튼으로 분리했다. 한 버튼에
  두 의미를 억지로 합치지 않은 이유: 기존 테스트가 `runButton.isEnabled()`를
  "지금 로드된 시나리오 하나가 유효한가"로 정확히 검증하고 있어서, 이
  버튼의 활성화 조건을 "체크된 게 있는가"로 바꾸면 그 테스트(잘못된 JSON
  재로드 시 `runButton`이 꺼져야 한다는 검증)가 깨진다.
- **선택 항목 실행**은 `fmpy.gui.scenario_batch.BatchRunner`를 그대로
  가져와 쓴다(복제하지 않음) — 체크된 파일들을 `run_many`로 돌리고, 각
  행이 실시간으로 갱신되고, 끝나면 **"판정 / 결과"** 탭에 종합 요약
  ("N개 중 합격 n · 불합격 n · ...")이 뜬다.
- **"판정 / 결과"** 탭에 과거 "여러 시나리오 실행" 탭에 있던 **"분석
  에이전트"** 영역(백엔드/모델 선택 + 분석 요청 + 결과 텍스트)을 그대로
  옮겼다 — `fmpy.gui.scenario_batch.AnalysisRunner`를 가져와 쓴다. 이미
  계산된 `summary`/`results`만 보여주고 **재실행하지 않는** 기존 원칙은
  그대로.
- **탭 구성이 5개에서 4개로 줄었다**: 시나리오 / 결과 그래프 / 판정 결과
  (분석 에이전트 포함) / 에이전트에게 요청. **"여러 시나리오 실행"** 탭은
  삭제했다(위젯·import 모두 제거, `self.batchTab` 속성 없음).
- `src/fmpy/gui/scenario_batch.py` **파일 자체는 지우지 않았다** —
  `BatchRunner`/`AnalysisRunner`는 재사용 중이고, 그 안의 독립형
  `ScenarioBatchDialog` 클래스와 그 테스트들은 (GUI에 더는 탭으로 끼워
  넣지 않지만) 여전히 올바른 코드이자 유효한 테스트라서 그대로 남겨뒀다.
- `tests/test_scenario_batch_gui.py`: `batchTab`/`showBatchTab()`에
  의존하던 통합 테스트 하나를 제거하고, 새 설계에 맞는 테스트 둘로
  교체했다 — `ScenarioDialog` 자체의 목록에 파일 두 개를 넣고
  `runCheckedFiles()`로 돌려 합격/불합격 종합을 확인하는 테스트, 그리고
  툴바에 `batchAction`/`agentChatAction`이 없고 시나리오 창 탭이 4개임을
  확인하는 테스트. `ScenarioBatchDialog`를 직접 쓰는 나머지 테스트는 전부
  그대로 둬도 통과함을 확인했다(클래스가 안 바뀌었으므로).
- `docs/scenario-runner-ko.md`/`docs/agent-integration-ko.md`: 창 구성
  설명, "여러 시나리오 실행" 관련 서술을 전부 새 구조(왼쪽 목록 + 판정/결과
  탭)로 고쳤다.

검증: `pytest tests/test_scenario_gui.py tests/test_scenario_batch_gui.py
tests/test_agent_chat.py tests/test_agent_settings.py -q` → **36 passed**.
이어서 `tests/` 전체에서 `-k "scenario or agent"`(깨진 무관 모듈
`test_examples.py`는 `dask` 미설치로 수집 단계에서 애초에 실패하므로
`--ignore`) → **121 passed, 2 skipped**(기존과 동일하게 Ollama 서버
없을 때 건너뛰는 것들). offscreen Qt로 `ScenarioDialog`를 직접 만들어
`hasattr(d, "batchTab")`이 `False`, 탭 4개, 파일 두 개 추가 시 중복 제거와
자동 로드·버튼 활성화가 기대대로 되는지 수동으로도 확인했다.

미완료: **커밋/푸시는 하지 않았다** — 요청 시 진행(이 세션은 매번 명시적
요청 전에는 커밋하지 않는 패턴을 유지 중).

### 추가 변경: 탭 이름 구분, 채팅 사이드바 오른쪽 이동, 분석 설정 버튼, 목록 숨기기 (2026-11-05, 이어서)

사용자가 새 다중 선택 UI를 쓰면서 네 가지를 지적/요청했다:

1. "에이전ㅌ트에게 요청이란 부분이 구분이 안됨. 시나리오 생성이라고 바꾸고."
   → 탭 이름을 혼동하기 쉬웠다는 지적. "에이전트에게 요청" 탭(생성
   에이전트)을 **"시나리오 생성"**으로 바꿨다 — "판정 / 결과" 탭 안의
   "분석 에이전트"와 이름이 겹쳐 보이는 문제를 없앴다. `scenario.py`의 탭
   라벨뿐 아니라 `agent_chat.py`의 `AgentChatDialog` 창 제목과 사이드바
   헤더 텍스트도 같이 바꿔서 어디서 봐도 같은 이름이 보이게 했다.
2. "에이전츠 채팅 창은 오른쪽으로" → `AgentChatDialog`의 `QSplitter` 순서를
   뒤집었다. 이제 **왼쪽이 FMU/백엔드/모델/결과 폴더 설정 + 결과 그래프**,
   **오른쪽이 채팅 사이드바**(대화 내용 + 입력창)다. 두 영역을 만드는
   `_buildMainArea`/`_buildChatSidebar` 메서드 자체는 손대지 않고
   `splitter.addWidget()` 호출 순서와 `setStretchFactor`/`setSizes`만
   바꿨다 — 두 메서드가 서로의 위젯을 참조하지 않아 순서 교체가 안전했다.
3. "판정/결과 창에 에이전트 설정하는 부분이 없음. 현재 에이전트에게
   요청하는창과 동일한건지?" → 확인 결과 **같은 설정 파일**
   (`agent_settings.load()`/`QSettings` INI)을 **"시나리오 생성"** 탭과
   공유하고 있었지만, "판정 / 결과" 탭 쪽에는 그걸 여는 버튼이 없었다.
   `ScenarioDialog`에 `openSettings()`/`applySettings()`를 새로
   추가하고(`AgentChatDialog`의 기존 패턴과 동일), 분석 에이전트 영역에
   **"설정..."** 버튼을 달았다. `applySettings()`는 자기 쪽 콤보박스를
   갱신하는 것과 동시에 `self.agentChatTab.applySettings(values)`도
   불러서 — 어느 쪽 설정 버튼에서 저장하든 **두 탭 다 바로 반영**된다
   (반대 방향, 즉 "시나리오 생성" 탭에서 저장했을 때 "판정 / 결과" 탭의
   콤보박스가 그 순간 바로 갱신되는 것까지는 안 했다 — 다음에 그 탭을 쓸 때
   `self.settings`를 다시 읽는 지점이 없어서, 필요하면 추가 요청).
4. "왼쪽에 시나리오 탭 구성은 잘했는데 숨기기 가능하게해주고" → FMU 입력
   칸 옆에 **"목록 숨기기"/"목록 보이기"** 토글 버튼을 추가했다. 왼쪽
   사이드바(`self.sidebar`)는 `QSplitter`의 자식이라 원래도 핸들을 끝까지
   끌면 접혔지만, 버튼으로 바로 접고 펼 수 있게 했다. 버튼은 스플리터
   바깥(항상 보이는 FMU 행)에 둬서, 사이드바를 숨긴 뒤에도 다시 꺼내는
   방법이 항상 보이게 했다.

변경 파일: `src/fmpy/gui/scenario.py`(토글 버튼/메서드, 분석 설정 버튼 +
`openSettings`/`applySettings`, 탭 라벨), `src/fmpy/gui/agent_chat.py`
(스플리터 순서, 창 제목, 사이드바 헤더), `src/fmpy/gui/MainWindow.py`
(툴바 툴팁·주석의 옛 탭 이름 갱신), `tests/test_agent_chat.py`(주석 하나의
옛 탭 이름 갱신 — 동작 자체를 검증하는 코드는 아니었음),
`docs/scenario-runner-ko.md`/`docs/agent-integration-ko.md`(탭 이름,
좌우 배치, 설정 버튼 공유, 분석 요청이 "선택 항목 실행" 결과에만 작동한다는
점 명시).

검증: `pytest tests/test_scenario_gui.py tests/test_scenario_batch_gui.py
tests/test_agent_chat.py tests/test_agent_settings.py -q` → **36 passed**
(기존과 동일한 개수 — 레이아웃/이름 변경이라 테스트가 깨지지 않을 거라
예상했고, 실제로도 안 깨졌다). offscreen Qt로 `ScenarioDialog`를 직접
`show()`해서(숨김 토글의 `isVisible()` 판정이 `show()` 없이는 항상
`False`로 나와 처음엔 헷갈렸다 — 최상위 창을 띄워야 자식의 가시성이
의미 있게 나온다) 탭 라벨 4개, 목록 숨기기/보이기 토글의 실제 가시성
전환, `analysisSettingsButton` 존재, `AgentChatDialog` 창 제목 변경을
직접 확인했다.

미완료: 커밋/푸시는 요청 시 진행. "시나리오 생성" 탭에서 설정을 바꿀 때
"판정 / 결과" 탭의 콤보박스를 그 순간 바로 갱신하는 역방향 동기화는
구현하지 않았다(필요하면 후속 요청).

### 이 세션의 실행 환경 (중요)

작업 디렉터리는 `C:\Users\nubur\fmpy_claude`이며 `origin/main`(`bb5a1e1`)의
새 클론이다. 기존 작업 저장소 `C:\Users\nubur\fmu\fmpy`는 **건드리지 않았고**
`git status`가 깨끗하며 같은 커밋을 가리킨다. 새 클론에는 `.venv`가 없으므로
기존 저장소의 `.venv`·네이티브 바이너리·`tests/resources/Reference-FMUs-dist`·
`examples/demo/Feedthrough.fmu`를 복사해 쓰고, `PYTHONPATH`로 이 클론의 `src`가
editable 설치(`C:\Users\nubur\fmu\fmpy\src`)보다 앞서게 해 테스트했다.
**변경 사항은 아직 커밋/푸시하지 않았다.** 어느 저장소를 정본으로 둘지와
커밋·푸시 여부는 사용자 확인이 필요하다.

## 최신 변경: 기존 메인 UI 결과 화면 연결 (2026-11-04)

- 사용자는 별도 시나리오 창뿐 아니라 기존 UI에서 실행 결과를 보고 싶다고 명확히 함.
- 시나리오 완료 시 결과를 기존 MainWindow.result의 NumPy structured array로 변환.
- 기존 결과 페이지, 변수 체크 목록, Save Result/Save Plotted Result와 연결.
- 메인 재생에서 시작한 실행은 완료 후 시나리오 창을 닫고 메인 결과 화면 표시.
- 시나리오 창에서 직접 실행해도 메인 결과 데이터가 갱신됨.
- 수집하지 않은 변수와 String은 메인 그래프에서 제외해 선택 오류 방지.
- 기존 메인 그래프에 필요한 plotly를 gui 의존성에 추가하고 현재 환경에 설치.
- 검증: 22개 테스트 통과. 메인 결과값, 결과 페이지, HTML 그래프 생성, 변수 선택/
  해제, 미수집 변수 선택, 기존 CSV 저장 동작까지 검증.

## 최신 변경: 메인 재생과 적용 시나리오 연결 (2026-11-04)

- 사용자 요청: 시나리오 폼을 닫은 뒤 메인 재생으로 같은 조건 실행.
- MainWindow에서 ScenarioDialog를 유지하고 검증된 JSON 로드 시 자동 적용.
- 메인 도구 모음에 적용 이름 및 **시나리오 해제** 추가.
- 적용 중 메인 재생은 시나리오 창을 열고 같은 실행기를 시작. 기본 설정은 무시.
- 창 닫기는 적용 상태를 유지. 해제 후 **메인 재생에 적용**으로 재적용 가능.
- FMU 열기/재로드, 잘못된 시나리오 로드 시 적용 해제. 앱 종료 후 영구 유지하지 않음.
- 메모리에 로드한 JSON을 사용하므로 디스크 수정은 다시 로드해야 반영됨.
- 검증: **22 tests passed**. 실제 MainWindow에서 JSON 로드→닫기→메인 재생→출력 7
  확인, 해제·재적용·FMU 재로드 시 해제까지 통합 테스트.

## 최신 변경: 결과 변수 선택 및 여러 그래프 (2026-11-04)

- 사용자 요청: 결과 목록에서 항목을 선택하고 여러 그래프를 확장해서 비교.
- `src/fmpy/gui/scenario_results.py` 추가, 시나리오 결과 탭에 연결.
- 그래프별 변수 체크 목록, 검색, 전체 선택/해제 지원.
- **그래프 추가**로 독립된 그래프를 추가하고 삭제 가능(최소 1개 유지).
- **확대 보기**는 현재 선택을 복사한 별도 창. 확대 창에서의 선택은 원본과 독립.
- 그래프는 세로 스크롤, 변수 목록과 그래프 사이 너비는 splitter로 조절.
- 수집된 outputs의 수치/Boolean만 목록에 표시. 문자열은 CSV에 유지.
- 실행을 다시 시작하면 기존 그래프 및 확대 창 초기화. 선택 구성의 영구 저장은 미구현.
- 검증: 실행기/GUI 테스트 **21개 통과**, 실제 MainWindow/ScenarioDialog 생성 통과.
  추가 테스트는 독립 선택, 검색 시 선택 유지, 확대 창, 삭제, 재실행 초기화를 확인.
- 사용자는 FMPy를 재시작한 뒤 Scenario...에서 시험을 실행하고 결과 그래프 탭에서 사용.

## 다음 작업자가 먼저 읽을 내용

- 작업 위치: `C:\Users\nubur\fmu\fmpy`
- 사용자 저장소: https://github.com/hazino/fmpy.git
- 원본: https://github.com/CATIA-Systems/FMPy.git (`upstream`)
- 브랜치: `main`. 아래 구현은 origin/main에 푸시 완료.
- Python: 현재 PC의 Python 3.13, 저장소 `.venv` 사용.
- 사용자 요청에 따라 **후속 작업을 마칠 때 이 로그에 변경·검증·미완료 사항을 추가**한다.
- 먼저 `git status --short`를 확인하고 사용자 변경은 보존한다.
- 구현은 완료되었지만 사용자의 실제 도메인 FMU는 아직 제공되지 않았다.
- 다음 사용자의 요청을 확인하고 진행한다. AI 연결이나 대규모 추가 기능을 임의로 시작하지 않는다.

## 사용자 목적과 합의한 구조

기존 소프트웨어를 FMI 2.0 Co-Simulation FMU로 포팅하여 시험하려 한다.
FMPy를 공통 시험 실행기로 사용하고, 추후 Ollama/Qwen 같은 로컬 모델이 자연어를
구조화된 시나리오로 변환하도록 연결할 계획이다.

역할을 분리한다:

1. 사람/GUI/AI가 시나리오 JSON을 작성한다.
2. 실행기는 FMU 인터페이스와 JSON을 검증한다.
3. FMPy로 초기화 → Set → doStep → Get → 결과 저장·판정을 수행한다.

특정 FMU 내부 코드를 알 필요는 없지만 변수 의미·단위·운전 제약·판정 기준은
도메인 자료로 제공되어야 한다. 전용 모델 학습은 현재 필수로 간주하지 않는다.
사용자는 CLI뿐 아니라 **GUI에서 시나리오 로드·실행**할 것을 명확히 요청했다.

## 구현 및 커밋 이력

| 커밋 | 내용 |
|---|---|
| `e42e288` | 기반 공식 FMPy 0.3.32 소스 |
| `91dcd5c` | FMI 2.0 Co-Simulation JSON 실행기, 검증, 테스트, 한국어 문서 |
| `2904d78` | Windows 설치·GUI 실행 PowerShell 스크립트 |
| `e50a40f` | GUI typing-extensions 누락 수정, 실제 메인 창 생성 검증 |
| `3fe947b` | 테스트 FMU 준비 스크립트와 5초 데모 시나리오 |
| `b3d3725` | GUI 시나리오 로드·실행·그래프·보고서·중지 및 통합 테스트 |

대상 저장소가 비어 있어 공식 FMPy 소스와 이력을 가져왔다.
최초 shallow clone 기반 push가 실패하여 upstream 이력을 unshallow로 가져온 뒤
정상 push했다. 현재 main은 origin/main을 추적한다.

## 주요 파일

- `src/fmpy/scenario.py`: JSON 검증, 타입별 Get/Set, 고정 간격 실행, 조건 규칙, 범위 판정, CLI.
- `src/fmpy/gui/scenario.py`: ScenarioDialog, QThread 기반 실행, 그래프와 결과 저장.
- `src/fmpy/gui/MainWindow.py`: 도구 모음 `Scenario...` 버튼 연결.
- `setup.ps1`: 가상환경 생성/재사용, 공식 wheel 바이너리 준비, GUI 의존성 설치·검증.
- `start-fmpy.ps1`: 로컬 가상환경으로 GUI 시작.
- `scripts/prepare_native.py`: 공식 fmpy==0.3.32 배포에서 네이티브 파일 복사.
- `scripts/check_gui.py`: offscreen으로 실제 MainWindow와 ScenarioDialog 생성 확인.
- `examples/demo/prepare.py`: 공식 Reference-FMUs v0.0.39 다운로드/캐시 검증 후 Feedthrough.fmu 추출.
- `examples/demo/scenario.json`: 0 → 10(1초) → 30(3초) → 0(4초), 5초 종료, 0.1초 간격.
- `examples/demo/README.md`: 데모 GUI/CLI 사용법.
- `docs/scenario-runner-ko.md`: 설치, 시나리오 형식과 정확한 실행 의미.
- `tests/test_scenario.py`, `tests/test_scenario_gui.py`: 실행기·GUI 테스트.

## 현재 PC에서 바로 실행

```powershell
cd C:\Users\nubur\fmu\fmpy
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-fmpy.ps1
```

GUI 도구 모음 **Scenario...** 클릭 → **FMU 선택...**에서
`examples\demo\Feedthrough.fmu` 선택 → **시나리오 로드...**에서
`examples\demo\scenario.json` 선택 → **시나리오 실행**.
메인 창에 FMU가 열려 있으면 시나리오 창에 자동 선택된다.

GUI는 JSON의 설정을 사용한다. 기본 Simulate 화면의 설정은 적용하지 않는다.
실행 후 결과 그래프 및 판정 탭을 제공하고 결과 폴더를 열 수 있다.
GUI 결과: `scenario-results/gui/<실행시각>/` 아래 results.csv, report.json, scenario.json.

CLI:

```powershell
.\.venv\Scripts\python -m fmpy.scenario examples/demo/Feedthrough.fmu examples/demo/scenario.json --output scenario-results/demo
```

신규 PC/재설치:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Launch
.\.venv\Scripts\python examples/demo/prepare.py
```

Python 3.10 이상과 인터넷이 필요하다. `.venv`, 네이티브 바이너리, FMU 파일,
결과 파일은 Git에 포함하지 않는다. 현재 PC에는 데모 FMU가 준비되어 있다.
새 clone에서는 prepare.py를 실행해야 한다. FMU는 새로 모델링한 것이 아니라
공식 Feedthrough 참조 모델 원본이다.

## 시나리오 의미와 현재 제한

- FMI 2.0 Co-Simulation 전용. 원래 FMPy의 다른 버전 지원은 제거하지 않았다.
- version=1, stop_time, step_size, outputs 필수. initial/events/rules/checks 선택.
- 고정 통신 간격; 종료와 이벤트는 시작 기준 간격 정수배. 최대 1,000,000 스텝.
- 초기값은 input 및 fixed/tunable parameter에만 설정. 실행 중 변경은 input만 허용.
- 시간 이벤트는 hold 입력. 중복된 동일 시각·변수 이벤트는 거부.
- 각 시점: 결과 기록 → 시간 이벤트 → 조건 평가 → 조건 동작 → doStep.
- 따라서 1초에 변경한 입력은 샘플 결과에 1.1초부터 나타난다. 모델 지연이 아니다.
- 조건은 통신 시점마다 평가하고 규칙별 최대 한 번 실행. 동시에 발생하면 배열 순서 적용.
- 종료 시각에는 기록만 하고 규칙을 평가하지 않는다.
- stop=true는 다음 doStep을 수행하지 않는다. 함께 설정한 입력의 이후 응답은 계산하지 않는다.
- checks는 초기값을 포함한 기록 구간의 수치 min/max. 조기 종료 시 수집 구간만 검사.
- checks가 없으면 passed=null. 실행 성공과 시험 합격을 구분한다.
- GUI 중지는 현재 네이티브 계산 구간이 반환된 다음 처리한다. 실행 중 창 닫기는 중지를
  요청하고 창을 유지하여 실행 중 QThread 파괴를 막는다.
- GUI는 문자열 출력을 CSV에 저장하며 수치/Boolean만 그래프에 표시한다.
- 램프/선형 보간, 조건 유지시간, 조건 후 지연, 복합 조건, 롤백, 비동기 stepping,
  여러 FMU 결합, AI/채팅 연결은 아직 구현하지 않았다.

## 검증 결과

최근 검증 (`b3d3725` 구현):

```powershell
.\.venv\Scripts\python -m pytest tests/test_scenario.py tests/test_scenario_gui.py -q
# 20 passed
.\.venv\Scripts\python scripts/check_gui.py
# MainWindow 및 ScenarioDialog 생성 성공
```

공식 Reference-FMUs v0.0.39의 실제 FMI 2.0 Feedthrough를 실행했다.
변수·시간·자료형 오류 거부, 시간 이벤트, 조건 입력 변경·종료, 합격·실패,
중지 후 재실행, GUI JSON 로드, QThread 실행, 그래프 데이터, CSV/보고서 저장을 확인했다.
5초 데모도 GUI 어댑터를 통해 실행하여 합격과 출력 변화를 확인했다.
전체 upstream 테스트 스위트는 실행하지 않았다. 실제 사용자 FMU 호환성은 미검증이다.

## 발생했던 문제와 조치

- 소스의 editable 설치가 누락된 native binaries 때문에 실패했다.
  공식 wheel 설치 → prepare_native.py → editable 설치 순서로 해결했다.
- 초기 설치 검증이 fmpy.gui 패키지 import만 하여 실제 창 import 오류를 놓쳤다.
  typing-extensions를 gui/complete 의존성에 추가하고 실제 창 생성 검증으로 교체했다.
- `qt.gui.icc: fromIccProfile: Failed to parse description` 경고가 사용자 환경에서 발생.
  사용자가 **창은 정상 열렸다**고 확인했다. 경고 숨김이나 색상 설정 변경은 하지 않았다.
- offscreen 그래프 캡처에서는 글꼴이 사각형으로 보였다. 실제 사용자 창의 글꼴 문제로
  확인된 것은 아니다. 코드 기능 테스트는 통과했으며 사용자 화면 시각 검증은 별도다.

## 이어서 할 때

1. 이 로그와 git 상태 확인.
2. 사용자가 새 GUI에서 데모를 실행할 수 있는지 확인하고 보고된 문제부터 해결.
3. 실제 FMU를 받으면 인터페이스를 inspect하고 도메인 조건에 맞는 JSON 작성·검증.
4. 추가 기능은 사용자 요청에 따라 구현하고 검증 결과를 이 로그에 기록.
