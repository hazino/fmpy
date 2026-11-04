# 작업 이어가기

- 시작 시 루트 `WORK_LOG.md`를 읽어 사용자 목적, 구현 상태, 실행법과 제한을 확인한다.
- 작업 전 `git status --short`를 확인하고 기존 사용자 변경을 보존한다.
- 후속 작업 완료 시 `WORK_LOG.md`에 변경 내용, 검증 결과, 미완료 사항을 기록한다.
- 실행은 저장소 `.venv`의 Python을 사용한다. FMI 시나리오 의미는
  `docs/scenario-runner-ko.md`에 정의되어 있다.
- GUI 변경 시 단순 패키지 import만으로 완료 판단하지 말고 실제 창 생성과
  변경한 흐름을 검증한다. 관련 테스트는 `tests/test_scenario_gui.py`이다.
