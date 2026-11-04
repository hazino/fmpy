# PID 데모: 실제 서드파티 코드로 만든 시험용 FMU

이 폴더는 "직접 손에 쥐고 돌려볼 수 있는" 예제입니다. 인터넷에서 가져온 **실제
동작하는 C 코드**를 FMI 2.0 Co-Simulation FMU로 감싸고, 그 코드를 **직접 읽고**
도출한 시나리오 7개를 함께 담았습니다. `fmpy.scenario_agent.plan`(XML 선언만
보는 자동 제안)과 비교할 근거 데이터도 아래에 있습니다.

## 무엇을 가져왔는가

[`pms67/PID`](https://github.com/pms67/PID) (c) 2020 Philip Salmony, **MIT
라이선스**. `src/PID.c`, `src/PID.h`는 **원본 그대로**이며 한 글자도 바꾸지
않았습니다. 라이선스 전문은 `src/LICENSE_PID.txt`에 있습니다. 70줄짜리
단일 PID 컨트롤러로, 비례·적분(사다리꼴 적분)·미분(대역 제한 미분기)과
**출력 클램프**, **적분기 anti-windup 클램프**를 구현합니다.

FMI 쪽 코드(`src/pid_fmi2.c`)만 이번에 새로 작성했습니다. FMI 2.0 Co-Simulation이
요구하는 함수 33개를 전부 구현하고, `FMU2Slave`가 실제로 로드하는 심볼과
정확히 일치하는지 `objdump`로 확인했습니다(누락·초과 없음).

## 빌드

```bash
cd examples/agent/pid_demo
bash build.sh
```

이 PC에서는 `C:\msys64\mingw64\bin\gcc.exe` (mingw-w64 gcc 15.2.0)로 빌드·검증
완료했습니다. `*.fmu`/`*.dll`은 이 저장소 전체 관례대로 git에 커밋하지
않으므로(`.gitignore`), 새로 clone하면 `build.sh`로 다시 만들어야 합니다.
다른 컴파일러를 쓰려면 `GCC=/path/to/gcc bash build.sh`, 다른 Python을 쓰려면
`PYTHON=/path/to/python bash build.sh`로 지정합니다.

## 검증 결과

```bash
python -m pytest tests/test_pid_demo.py -q
# 9 passed
```

`review()`로 확인한 선언 품질 — **blocking 0건**, medium 2건(단위·범위 미선언)뿐입니다.
같은 도구로 본 공식 Feedthrough 참조 FMU는 missing_description 14건을 포함해
훨씬 많은 결함이 나옵니다(`docs/agent-integration-ko.md` 참고). 이 FMU는 일부러
선언을 풍부하게 썼습니다(출력 4개 전부의 정확한 의존 관계, 각 변수 설명).

`plan()`(XML 선언만 보고 자동 제안)은 5개를 뽑습니다: `smoke`,
`step-setpoint`, `step-measurement`, `dependency-coverage`,
`parameters-at-declared-bounds`. **전부 돌아가지만, anti-windup·미분 킥·초기화
실패 같은 건 하나도 못 찾습니다** — 선언에는 "무엇에 의존하는가"만 있고
"클램프가 몇에서 걸리는가"는 코드 안에만 있기 때문입니다. 아래 7개는 그 코드를
**직접 읽어서** 만든 것입니다.

## 시나리오 7개 (`scenarios/`)

| 파일 | 무엇을 겨냥했는가 | 코드의 어디를 읽고 도출했는가 | 실제 확인된 결과 |
|---|---|---|---|
| `01_step_response.json` | 비례+적분 응답, 클램프 걸리기 전 | `out = Kp*error + integrator + differentiator`, 클램프 전 | output이 0→7.19로 매끄럽게 성장, 10(한계) 근처 안 감 |
| `02_measurement_step_derivative_kick.json` | 측정값 변화가 만드는 미분 킥과 감쇠 | 미분기 공식의 `(measurement - prevMeasurement)` 항 | differentiator_state가 8.0까지 치솟은 뒤 ~tau 시간에 걸쳐 거의 0으로 감쇠 |
| `03_anti_windup_clamp.json` | 적분기가 선언된 한계에서 정확히 멈추는가 | `if (integrator > limMaxInt) integrator = limMaxInt` | integrator_state가 정확히 5.0에서 멈추고 유지됨 |
| `04_output_saturation.json` | 두 클램프(출력/적분기)가 다른 시점에 걸리는가 | 출력 클램프와 적분기 클램프가 코드에서 분리된 두 문장 | output은 즉시 10.0 포화, integrator_state는 아직 3.05(미포화) |
| `05_setpoint_step_no_derivative_kick.json` | setpoint 변화는 미분항을 건드리지 않는가 | 코드 주석: "derivative on measurement, therefore minus sign" | differentiator_state가 전 구간 정확히 0 (02와 대조) |
| `06_communication_step_mismatch_warns.json` | 통신 간격과 파라미터 T 불일치를 FMU가 스스로 보고하는가 | `pid_fmi2.c`의 `fmi2DoStep` 안 자체 점검 | 10스텝 모두 경고 로그 10건 캡처됨 |
| `07_invalid_integrator_bounds_fails_init.json` | 말이 안 되는 설정(limMinInt>limMaxInt)을 초기화 단계에서 거부하는가 | `fmi2ExitInitializationMode`의 유효성 검사 | **의도적으로 실패**: `fmi_call_failed`, "fmi2ExitInitializationMode failed with status 3" |

`02`와 `06`은 숫자 판정(`checks`)이 없습니다. 02는 그래프로 보는 게 목적이고,
06은 `report.json`의 `fmu_log`/`summary.log_warnings`를 보는 게 목적입니다.
`07`은 **실행 자체가 실패하는 게 정답**입니다 — GUI나 CLI에서 이게 "에러"로
보이면 제대로 동작하는 겁니다.

## 직접 돌려보기

**GUI:**
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-fmpy.ps1
```
도구 모음 **Scenario...** → **FMU 선택...**에서 `examples/agent/pid_demo/PID.fmu` 선택.

- 직접 쓴 시나리오를 보려면: **시나리오 로드...**에서 `scenarios/` 아래 파일 하나를
  선택 → **시나리오 실행** → **결과 그래프** 탭에서 확인.
- XML만 보고 자동으로 뽑은 제안과 비교하려면: **시나리오 제안 보기...**를 눌러
  5개 제안을 보고 원하는 것만 체크해서 실행.

**CLI:**
```powershell
.\.venv\Scripts\python -m fmpy.scenario_agent run `
  --fmu examples/agent/pid_demo/PID.fmu `
  --scenario examples/agent/pid_demo/scenarios/03_anti_windup_clamp.json
```

**코드에서 전부 자동으로:**
```powershell
.\.venv\Scripts\python examples/agent/plan_and_run.py examples/agent/pid_demo/PID.fmu
```

## 이게 왜 "에이전트가 코드를 읽고 시나리오를 만든 것"인가

7개 시나리오는 `plan()`처럼 XML을 기계적으로 파싱해서 나온 게 아닙니다.
`src/PID.c`의 실제 수식(사다리꼴 적분, anti-windup 클램프, derivative-on-
measurement)과 `src/pid_fmi2.c`의 자체 점검 로직(T 불일치, 경계값 역전)을
**직접 읽고**, 각 줄기마다 "이 분기/임계값을 실제로 건드리는 입력"을 역산해서
만들었습니다 — 이전 대화에서 설명한 "에이전트가 소스를 읽고 시나리오를
도출한다"의 수동 버전입니다. 자동화된 LLM 파이프라인(Ollama 등)은 아직
연결되어 있지 않지만, 여기서 나온 7개 JSON은 그 파이프라인이 만들어내야 할
결과물의 실제 예시이자 품질 기준입니다.
