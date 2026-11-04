# 시나리오 로드 체험

`Feedthrough.fmu`는 Modelica Association의 공식 Reference-FMUs v0.0.39에서
가져온 FMI 2.0 샘플입니다. 입력을 같은 자료형의 출력으로 전달하는 모델이므로
물리 모델 지식 없이 Set → doStep → Get을 확인할 수 있습니다.
원본: https://github.com/modelica/Reference-FMUs (BSD-2-Clause).
FMU 파일은 변경하지 않고 사용하며 소스 저장소에는 바이너리를 커밋하지 않습니다.

저장소 루트에서 다음 명령으로 준비합니다(인터넷 연결 필요, 다운로드 SHA-256 확인).

```powershell
.\.venv\Scripts\python examples/demo/prepare.py
```

FMPy GUI 도구 모음에서 **Scenario...** 버튼을 누릅니다.

1. **FMU 선택...** → `examples/demo/Feedthrough.fmu` (메인 창에서 열었다면 자동 선택)
2. **시나리오 로드...** → `examples/demo/scenario.json`
3. 검증 완료 문구와 JSON 내용을 확인하고 **시나리오 실행** 클릭
4. **결과 그래프**, **판정 / 결과** 탭에서 확인
5. **결과 폴더 열기**로 CSV·보고서·실행한 시나리오 확인

JSON 로드 후 창을 닫고 **메인 화면의 재생 버튼**으로 실행해도 같은 시나리오가
적용됩니다. 상단에 시나리오 이름이 표시되며 **시나리오 해제**를 누르면 기본
실행으로 돌아갑니다. 다른 FMU를 열거나 다시 로드하면 적용을 해제합니다.

GUI는 `scenario-results/gui` 아래 실행별 폴더에 저장합니다. 이 실행에서는
JSON의 설정을 사용하며 기본 Simulate 화면의 설정은 적용하지 않습니다.
실행 중 중지를 요청하면 현재 FMU 계산 구간 종료 후 중지합니다.

명령줄에서도 같은 실행기를 사용할 수 있습니다.

```powershell
.\.venv\Scripts\python -m fmpy.scenario examples/demo/Feedthrough.fmu examples/demo/scenario.json --output scenario-results/demo
```

첫 경로는 FMU, 두 번째 경로는 로드할 시나리오입니다. 다른 시나리오로 시험하려면
JSON을 복사·수정한 뒤 두 번째 경로를 바꿉니다.

| 시각 | 입력 |
|---|---:|
| 초기 | 0 |
| 1초 | 10 |
| 3초 | 30 |
| 4초 | 0 |
| 5초 | 종료 |

통신 간격은 0.1초입니다. 출력이 0~30 범위이면 합격합니다.
이 실행기는 각 시점의 값을 기록한 뒤 입력을 변경하므로 변경값은
1.1초, 3.1초, 4.1초 기록부터 나타납니다. 이는 모델의 물리적 지연이 아닌
기록 순서에 따른 것입니다.

결과는 `scenario-results/demo/results.csv`와 `report.json`에 저장됩니다.
재실행하면 같은 출력 폴더의 결과를 덮어씁니다.
