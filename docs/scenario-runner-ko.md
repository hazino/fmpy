# FMI 2.0 시나리오 실행기

이 저장소는 공식 CATIA-Systems/FMPy 소스에 `fmpy.scenario`를 추가한 버전입니다.
추가 실행기는 FMI 2.0 Co-Simulation 전용이며 기존 FMPy 기능은 유지합니다.
FMU 내부 구현을 몰라도 사용할 수 있지만 변수 의미·단위·정상 동작 기준은
시험 작성자가 알아야 합니다. AI, Ollama, 채팅 GUI는 포함하지 않습니다.

## 설치 (Windows PowerShell, Python 3.10 이상)

저장소 루트에서 실행합니다. 공식 Git 소스에는 빌드된 네이티브 라이브러리가
빠져 있으므로 동일 버전의 공식 배포 패키지에서 바이너리를 준비합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install fmpy==0.3.32 pytest requests
.\.venv\Scripts\python scripts/prepare_native.py
.\.venv\Scripts\python -m pip install -e . --no-deps
```

## 사용

```powershell
# FMU 변수·유형 조회: 시나리오를 작성하기 전에 확인
.\.venv\Scripts\python -m fmpy.scenario model.fmu --inspect

# 네이티브 FMU를 실행하지 않고 구조와 변수 매핑 검증
.\.venv\Scripts\python -m fmpy.scenario model.fmu scenario.json --validate-only

# 시험 실행
.\.venv\Scripts\python -m fmpy.scenario model.fmu scenario.json --output results/run-001
```

`results.csv`는 통신 시점별 측정값, `report.json`은 범위 판정·종료 이유·발생한
규칙 인덱스를 담습니다. 출력 폴더가 같으면 결과 파일을 덮어씁니다.
종료 코드: 정상 완료/검증 성공 0, 판정 실패 1, 잘못된 입력 2.
네이티브 실행 오류는 예외로 전달되며 성공 보고서를 생성하지 않습니다.
판정 기준이 없으면 `passed`는 `null`입니다. 실행 성공을 시험 합격으로 간주하지 않습니다.

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

현재 형식은 고정 통신 간격, hold 입력, 단일 조건·일회성 동작과 범위 판정을
지원합니다. 램프/선형 보간, 조건 유지 시간, 조건 충족 후 지연, 복합 조건,
롤백·비동기 stepping·여러 FMU 결합은 후속 확장 범위입니다.
이벤트 검출 정확도는 통신 간격에 제한됩니다. 대상 OS/CPU에 맞는 FMU 바이너리와
외부 의존성이 필요하며, 해당 FMU가 선택한 고정 간격을 수용해야 합니다.

## 검증

```powershell
.\.venv\Scripts\python -m pytest tests/test_scenario.py -q
```

테스트는 공식 Reference-FMUs v0.0.39를 다운로드하고 기존 FMPy fixture의
SHA-256 검사를 사용합니다. Feedthrough FMI 2.0 FMU로 시간별 입력, 조건 동작,
조기 종료, 자료형, 합격·실패 판정을 확인합니다.
`examples/scenarios/feedthrough.json`은 이 참조 FMU에 바로 사용할 수 있습니다.
