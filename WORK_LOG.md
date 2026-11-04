# 작업 로그 및 이어하기 안내

마지막 기록: 2026-11-04 (Asia/Seoul)

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
