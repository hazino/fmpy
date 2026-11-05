# FMI 2.0 시나리오 실행기

이 저장소는 공식 CATIA-Systems/FMPy 소스에 `fmpy.scenario`를 추가한 버전입니다.
추가 실행기는 FMI 2.0 Co-Simulation 전용이며 기존 FMPy 기능은 유지합니다.
FMU 내부 구현을 몰라도 사용할 수 있지만 변수 의미·단위·정상 동작 기준은
시험 작성자가 알아야 합니다. 시나리오 GUI를 제공하며 AI, Ollama, 채팅 기능은 포함하지 않습니다.
프로그램이나 에이전트가 붙는 JSON 전용 도구 계층은 `fmpy.scenario_agent`이며
사용법은 [에이전트 연동 구조](agent-integration-ko.md)에 있습니다.

## 설치 (Windows PowerShell, Python 3.10 이상)

Python 3.10 이상과 인터넷 연결이 필요합니다. 저장소 루트에서 아래 한 줄로
가상환경 생성, 네이티브 라이브러리 준비, GUI·시험 의존성 설치와 설치 검증을 수행합니다.
기존 `.venv`가 있으면 재사용하며, 재실행 시에도 로컬 수정 소스를 유지합니다.
Python 자체는 설치되어 있어야 합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Launch
```

`-Launch`를 빼면 설치만 합니다. 실행 정책 우회는 해당 PowerShell 프로세스에만
적용되며 시스템 설정은 바꾸지 않습니다. 다른 Python을 사용하려면
`-Python "C:\Path\To\python.exe"`를 지정합니다(가상환경 최초 생성 시 적용).

설치 후 GUI 실행:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-fmpy.ps1
```

아래는 스크립트 없이 수동으로 설치하는 방법입니다.

저장소 루트에서 실행합니다. 공식 Git 소스에는 빌드된 네이티브 라이브러리가
빠져 있으므로 동일 버전의 공식 배포 패키지에서 바이너리를 준비합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install fmpy==0.3.32 pytest requests
.\.venv\Scripts\python scripts/prepare_native.py
.\.venv\Scripts\python -m pip install -e ".[gui]"
```

## 사용

GUI에서는 도구 모음의 **Scenario...** → **FMU 선택...** → **시나리오 로드...** →
**시나리오 실행** 순서로 사용합니다. 메인 창에서 연 FMU는 자동 선택됩니다.

메인 창 툴바에는 시나리오 관련 버튼이 **"Scenario..." 하나뿐**입니다. 이게
여는 창에 탭이 다섯 개 있습니다: **시나리오**(JSON 로드·실행) / **결과
그래프** / **판정 / 결과** / **에이전트에게 요청** / **여러 시나리오 실행**.
뒤의 두 탭은 별도 창이 아니라 이 창 안에 통합되어 있으며, FMU는 맨 위의
FMU 입력 칸을 공유합니다 — 거기서 FMU를 바꾸면 두 탭에도 즉시 반영됩니다.
탭이 다섯 개나 되고 그래프·표를 넓게 보려면 화면을 꽉 채우는 게 편해서,
이 창은 **최대화할 수 있습니다**(제목 표시줄의 최대화/최소화 버튼, 또는
더블클릭).

**여러 시나리오 실행** 탭: 직접 쓴 시나리오 JSON 여러 개를 한 번에 돌리고
합격/불합격을 종합합니다. **파일 추가...** 또는 **폴더에서 추가...**로
목록을 만들고, 체크박스로 원하는 것만 골라 **선택 실행**하면 FMU 하나에
대해 전부 돌리고, 위쪽에 "7개 중 합격 5 · 불합격 0 · 판정 기준 없음 1 ·
실행 불가 1" 같은 요약이 뜹니다. 각 행을 선택하면 실패한 검사·FMU 로그
건수·저장 위치가 아래 패널에 나오고, **보고서 저장...**으로 Markdown 요약을
파일로 뽑을 수 있습니다. 결과는 `<결과폴더>/<파일이름>/`에 개별적으로도
저장됩니다.

실행이 끝나면 그 아래 **분석 에이전트** 영역에서 백엔드·모델을 고르고
**분석 요청**을 누르면, 방금 나온 결과 표를 모델에게 보여주고 자연어
설명을 받을 수 있습니다. **다시 실행하지 않습니다** — 이미 나온 숫자만
봅니다. 자세한 내용과 실제로 돌려본 결과는
[에이전트 연동 구조](agent-integration-ko.md)에 있습니다.

**에이전트에게 요청** 탭: 왼쪽은 **채팅 사이드바**(대화 내용 + 입력창,
항상 보임), 오른쪽은 FMU/백엔드/결과 폴더 설정과 **결과 그래프**가 같이
있는 영역입니다 — 탭을 왔다갔다할 필요 없이 채팅하면서 그래프가 바로
갱신됩니다. 사이드바에 "setpoint를 0.2초에 0에서 2로 올리고 output이
-10~10 안에 있는지 확인" 같이 자연어로 적으면, 선택한 백엔드(Ollama 로컬
모델 또는 Claude API)가 시나리오 JSON을 만들고, 검증 실패 시 자동으로
교정 재시도하고, 통과하면 실제로 실행까지 합니다. **모델은 JSON 텍스트만
만들고 FMU를 직접 건드리지 않습니다** — 검증·실행은 항상 fmpy 코드가
수행합니다. 결과는 대화창에 요약되고 오른쪽 그래프에 바로 그려지며, 생성된
시나리오는 저장하거나 결과 폴더를 열어 확인할 수 있습니다.

이 탭의 **설정...** 버튼에서 기본 백엔드·Ollama 모델/Host·최대 재시도
횟수·Claude 기본 모델/최대 턴 수·Claude API 키(선택)·기본 결과 폴더를
저장해둘 수 있습니다. 설정은 이 PC의 설정 파일(사용자 프로필 아래 INI
파일, 레지스트리 아님)에 저장되고 다음에 창을 열 때도 유지됩니다. API 키를
비워두면 환경 변수 `ANTHROPIC_API_KEY`를 그대로 씁니다 — 키를 입력하면
그 설정 파일에 평문으로 저장된다는 점을 툴팁에서 알려줍니다.

자세한 사용법과 실제로 돌려본 결과(Ollama)는
[에이전트 연동 구조](agent-integration-ko.md)에 있습니다.

직접 JSON을 작성하지 않고 FMU가 선언한 구조에서 시나리오 후보를 보려면
**시나리오 제안 보기...** 를 누릅니다. FMU만 선택되어 있으면 되고 JSON 로드는
필요 없습니다. 제안 목록(스모크 테스트, 입력별 계단 변경, 의존 관계 커버리지,
파라미터 한계값)이 표로 나오고, 체크박스로 원하는 것만 골라 **선택 실행**할 수
있습니다. 실행은 자동으로 진행되지 않고 사용자가 고른 것만 실제 FMU로 돌립니다.
행을 선택하면 아래 패널에 근거·대상 변수·필요한 도메인 정보·(실행했다면) 결과
요약과 저장 위치가 나옵니다. 이 제안들에는 **합격 기준이 없습니다** — FMU는
자기가 무엇을 해야 하는지 말하지 않으므로, 결과는 "제대로 도는지"만 보여주고
"맞는지"는 직접 판단해야 합니다. 결과는 FMU 하나당, 제안 하나당 실행 시각별
폴더에 CSV/report.json/scenario.json으로 저장됩니다.
유효한 JSON을 로드하면 메인 재생에도 자동 적용됩니다. 창을 닫은 뒤 메인 화면의
재생을 누르면 적용된 시나리오를 실행하고 결과 창을 엽니다. 도구 모음에 적용된
시나리오 이름이 표시되며 **시나리오 해제**로 기본 실행으로 돌아갑니다.
해제 후 다시 적용하려면 시나리오 창의 **메인 재생에 적용**을 누릅니다.
FMU를 열거나 다시 로드하면 적용 상태를 해제합니다. 앱을 종료하면 상태는 유지되지
않으며, 디스크의 JSON 수정은 다시 로드해야 반영됩니다.
JSON을 미리 보고 검증하며, 실행 후 그래프·판정·결과 폴더를 확인할 수 있습니다.
GUI의 실행 설정은 JSON을 기준으로 하며 기본 Simulate 설정과 별개입니다.
결과는 선택한 저장 위치 아래 실행별 폴더에 CSV, report.json, scenario.json으로 저장합니다.
문자열 출력은 CSV에서 확인하고 수치·Boolean 출력은 그래프로 표시합니다.
결과 그래프의 변수 목록에서 체크하여 표시 항목을 바꿀 수 있습니다. 검색은 목록만
필터링하며 선택을 바꾸지 않습니다. **그래프 추가**로 독립된 그래프를 생성하고 각
그래프에 다른 변수를 선택할 수 있습니다. **확대 보기**는 선택을 복사한 별도 창을
열며 그 창의 선택은 원본과 독립적입니다. 여러 그래프는 세로 스크롤로 확인합니다.
표시 가능한 변수는 해당 시나리오의 outputs에 포함되어 수집된 변수입니다.
재실행하면 이전 그래프 구성과 확대 창은 초기화됩니다.
시나리오 실행 결과는 **기존 메인 UI의 결과 페이지에도 연결**됩니다. 메인 재생으로
시작하면 완료 후 메인 결과 화면으로 돌아갑니다. 기존 변수 체크 목록으로 표시를
바꾸고 Save Result/Save Plotted Result로 저장할 수 있습니다. 시나리오 창에서
직접 실행한 경우에도 메인 결과가 갱신됩니다. 수집되지 않은 변수는 그래프에 표시하지 않습니다.
범위 판정 실패도 그래프와 보고서에 표시됩니다.

아래 명령줄 방식도 계속 지원합니다.

```powershell
# FMU 변수·유형 조회: 시나리오를 작성하기 전에 확인
.\.venv\Scripts\python -m fmpy.scenario model.fmu --inspect

# 네이티브 FMU를 실행하지 않고 구조와 변수 매핑 검증
.\.venv\Scripts\python -m fmpy.scenario model.fmu scenario.json --validate-only

# 시험 실행
.\.venv\Scripts\python -m fmpy.scenario model.fmu scenario.json --output results/run-001

# FMU의 디버그 로그 메시지까지 요청
.\.venv\Scripts\python -m fmpy.scenario model.fmu scenario.json --debug-logging
```

FMU가 선언한 구조에서 시나리오 후보를 뽑으려면 `plan`을 씁니다.

```powershell
.\.venv\Scripts\python -m fmpy.scenario_agent plan --fmu model.fmu
```

`results.csv`는 통신 시점별 측정값, `report.json`은 판정·종료 이유·발생한 규칙과
그 이유·출력별 요약·FMU 로그를 담고, `scenario.json`은 실행한 시나리오를 그대로
남깁니다. 출력 폴더가 같으면 결과 파일을 덮어씁니다.
종료 코드: 정상 완료/검증 성공 0, 판정 실패 1, 잘못된 입력 2.
검증 실패는 사람이 읽는 문장 대신 `{"errors": [{"code", "path", "message"}]}` JSON을
표준 오류로 출력합니다. 네이티브 실행 오류는 예외로 전달되며 성공 보고서를 만들지
않습니다. 판정 기준이 없으면 `passed`는 `null`입니다. 실행 성공을 시험 합격으로
간주하지 않습니다.

`report.json`에 들어가는 항목:

- `summary.outputs[변수]`: 수치는 min/max와 그 시각, 첫값·끝값·평균·비유한값 개수.
  Boolean/String은 변화 시점(최대 20개), Boolean은 true/false 개수, String은 서로 다른 값.
- `checks[]`: `passed`, `first_failure_time`, `failure_count`, `margin`(통과 시 남은 여유,
  실패 시 넘어간 폭의 음수), `worst`, `worst_time`.
- `rule_events[]`: 발생 시각, 조건, 그 시점의 실제 값 `trigger.actual`, 적용한 입력, 중지 여부.
- `fmu_log[]`: FMU가 자기 로거로 보낸 메시지를 시각·등급·카테고리와 함께 최대 200건.
  개수는 `summary.log_errors`/`log_warnings`로 먼저 확인합니다.
- `termination`: `stop_time`, `rule`, `fmu_terminated` 중 하나.

## 시나리오 예시

아래 변수명은 예시입니다. 실제 FMU에 맞게 바꿉니다.

```json
{
  "version": 1,
  "start_time": 0.0,
  "stop_time": 5.0,
  "step_size": 0.01,
  "initial": {"speed_command": 0.0},
  "events": [
    {"time": 1.0, "set": {"speed_command": 1000.0}},
    {"time": 4.0, "set": {"speed_command": 0.0}}
  ],
  "rules": [
    {
      "when": {"variable": "temperature", "op": ">=", "value": 80.0},
      "set": {"speed_command": 0.0},
      "stop": true
    }
  ],
  "outputs": ["actual_speed", "temperature"],
  "checks": [{"variable": "temperature", "min": -20.0, "max": 80.0}]
}
```

- 시간은 초입니다. `step_size`는 외부 통신 간격이며 FMU 내부 솔버 간격은 아닙니다.
- `initial`은 입력과 fixed/tunable 파라미터만 설정합니다. 초기화 전에 적용합니다.
- `events`는 입력만 변경합니다. 값은 다음 변경까지 유지됩니다(hold).
- 종료 시각과 이벤트 시각은 시작 시각 기준 통신 간격의 정수배여야 합니다.
  이벤트는 종료 시각 이전이어야 합니다. 같은 시각·변수의 중복 이벤트는 거부합니다.
- 시작 시각 이벤트는 초기화 전에 initial을 덮어씁니다.
- 각 통신 시점에는 먼저 결과를 기록하고, 시간 이벤트를 적용한 후 조건을 평가합니다.
  따라서 t에서 바꾼 입력의 계산 결과는 일반적으로 다음 기록에서 확인합니다.
- `name`, `description`, `metadata`는 선택 항목입니다. `events`/`rules`/`checks`의 각
  항목에는 `id`와 `description`을 붙일 수 있고 `id`는 배열 안에서 유일해야 합니다.
  `metadata`는 보고서에 그대로 보존되고 `id`는 `rule_events`·`checks`에 나타납니다.
- `rules`는 통신 시점에만 평가하며 각 규칙은 최대 한 번 실행됩니다.
  지원 연산자는 `==`, `!=`, `>`, `>=`, `<`, `<=`입니다.
  Boolean/String에는 동등·부등 비교만 가능합니다.
- 조건들은 규칙의 동작을 적용하기 전에 평가합니다. 동시에 발생한 동작은
  배열 순서로 적용되며 동일 변수는 뒤 규칙이 우선합니다. 규칙은 시간 이벤트보다 우선합니다.
- 종료 시각에는 마지막 결과만 기록하며 이벤트·규칙은 실행하지 않습니다.
- `stop: true`는 다음 doStep을 실행하지 않고 종료합니다. 함께 설정한 입력에 대한
  응답까지 확인하려면 stop을 빼고 별도 종료 규칙이나 종료 시간을 사용합니다.
- `checks`는 초기 샘플을 포함한 모든 기록값의 min/max 범위를 검사합니다.
  첫 실패 시각을 보고하며, 조기 종료 시에는 수집된 구간만 판정합니다.
- Real/Integer/Enumeration/Boolean/String을 지원합니다. min/max가 FMU에 있으면
  설정값을 검사합니다. 단위 변환은 하지 않습니다.
- 입력 이름·자료형·쓰기 권한, 알 수 없는 옵션, NaN/Infinity, 비정상 시간값을 거부합니다.
  FMI 1/3 또는 Model Exchange 전용 FMU는 실행하지 않습니다.
- 검증은 서로 독립적인 문제를 **한 번에 모두** 보고합니다. 각 문제는 고정된 `code`와
  시나리오 안의 위치 `path`(예: `events[2].set.u`)를 가지며, 변수 이름이 틀린 경우
  가까운 후보를 `candidates`로 함께 돌려줍니다.
- FMU가 doStep을 discard하면서 종료를 요청하면 수집한 결과와 판정을 유지한 채
  `termination`을 `fmu_terminated`로 보고합니다. 오류로 처리하지 않습니다.
- Boolean 출력은 JSON의 `true`/`false`로 기록합니다(이전에는 1/0). 시나리오에 쓰는
  Boolean 표기와 같아지므로 기록한 값을 그대로 다시 설정할 수 있습니다.

## Python 및 향후 AI 연결

```python
import json
from fmpy.scenario import run_scenario

with open("scenario.json", encoding="utf-8") as stream:
    scenario = json.load(stream)
result = run_scenario("model.fmu", scenario)
```

사람·GUI·AI가 같은 JSON을 생성하도록 구성합니다. AI에는 변수 설명과 도메인
문서를 별도로 제공하고 생성된 JSON을 이 실행기로 검증·실행하면 됩니다.
실행기는 자연어 해석이나 임의 Python 실행을 수행하지 않습니다.

JSON Schema와 실행기 능력 선언은 `fmpy.scenario_schema`에 있고
`python -m fmpy.scenario_agent schema`로 꺼냅니다. 모델에 줄 근거 자료는
`context` 도구가 한 객체로 만들어 줍니다. 자세한 절차는
[에이전트 연동 구조](agent-integration-ko.md)를 보십시오.

현재 형식은 고정 통신 간격, hold 입력, 단일 조건·일회성 동작과 범위 판정을
지원합니다. 램프/선형 보간, 조건 유지 시간, 조건 충족 후 지연, 복합 조건,
롤백·비동기 stepping·여러 FMU 결합은 후속 확장 범위입니다.
램프 입력은 기존 CSV 입력 경로(`simulate_fmu(input=...)`)만 지원합니다.
두 기능의 차이는 에이전트 연동 문서의 비교 표에 정리했습니다.
이벤트 검출 정확도는 통신 간격에 제한됩니다. 대상 OS/CPU에 맞는 FMU 바이너리와
외부 의존성이 필요하며, 해당 FMU가 선택한 고정 간격을 수용해야 합니다.

## 검증

```powershell
.\.venv\Scripts\python -m pytest tests/test_scenario.py tests/test_scenario_agent.py tests/test_scenario_gui.py -q
```

테스트는 공식 Reference-FMUs v0.0.39를 다운로드하고 기존 FMPy fixture의
SHA-256 검사를 사용합니다. Feedthrough FMI 2.0 FMU로 시간별 입력, 조건 동작,
조기 종료, 자료형, 합격·실패 판정을 확인합니다.
`examples/scenarios/feedthrough.json`은 이 참조 FMU에 바로 사용할 수 있습니다.
