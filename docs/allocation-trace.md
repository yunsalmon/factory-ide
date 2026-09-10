# 할당 전후 상태 기록 (schema_version 2)

실행 뒤 **할당 전후 비교** 탭에서 선택 기록을 고르면 같은 할당의 후보 평가 전과 배정 확정 후를 나란히 볼 수 있습니다. 변경 행은 노란색입니다. 전/후 이동 버튼은 재생 커서를 해당 경계로 이동시키며, 타임라인을 이동하면 해당 이벤트의 할당으로 비교가 갱신됩니다. 전후 상태는 이동 도착이나 처리 완료를 뜻하지 않습니다.

Pull의 `assigned`는 머신 예약을 확정합니다. 로트는 아직 출발 위치에서 대기하며 다음 `move`부터 이동합니다. Push의 `assigned`는 목적지 큐에 넣으며 기존 머신 점유를 바꾸지 않습니다. 머신은 `idle`, `reserved`, `processing`을 구분합니다. OUTPUT 목적지 배정 뒤 이동·완료도 동일한 할당 ID를 사용합니다.

JSON 내보내기의 `allocations`는 다음 필드를 갖습니다.

- `id`: 실행 내 선택 순서로 부여한 `ALLOC-000001` 형식의 안정적 ID.
- `lot_id`, `route_id`, `destination`, `mode`: 선택 결과.
- `decision_index`: 선택 이벤트의 0 기반 인덱스.
- `before_cursor`: 후보 평가 직전까지 소비한 이벤트 개수.
- `after_cursor`: `assigned`까지 소비한 이벤트 개수.

각 이벤트는 `allocation_id`로 결정·배정·이동·처리를 연결합니다. `state_changes.lots`와 `state_changes.machines`는 ID별 전체 레코드를 교체하는 차분입니다. 초기 로트 집합은 비어 있고 모델의 머신은 모두 `{state: "idle", lot: null}`입니다. 이벤트 배열 앞에서 커서 개수만큼 차분을 적용하면 상태를 재현할 수 있습니다. 시간값으로 이벤트를 합치면 안 됩니다. 동일 시각에도 선택과 확정은 서로 다른 이벤트입니다. 이벤트와 모델은 복사해 보관하므로 실행 중 후속 변경이 과거 기록을 수정하지 않습니다.

대기 목록은 `waiting`인 로트에서 예약/처리 머신이 가진 로트를 제외하고, `target`이 있으면 목적지 큐, 없으면 `location`에 묶습니다. 순서는 `ready_since`, 로트 ID 오름차순입니다. 이는 대기열의 표시 순서이며 사용자 Pull 선택 함수를 대신하는 우선순위가 아닙니다.

`choose_candidate`는 `(후보 ID, 이유)` 또는 `(None, 이유)`를 반환할 수 있습니다. `None`은 이번 선택 보류를 뜻하며 성공한 할당이나 이동을 만들지 않습니다. Pull은 다음 공장 상태 변경 때 다시 평가하고, Push는 해당 준비 단계에서 보류 상태로 남습니다. 경로가 없으면 `blocked`를 기록합니다. 미선택과 경로 없음은 전후 비교에 표시되지만 `allocations`에는 들어가지 않습니다. 조건 제외 사유와 함수의 선택 이유를 기록하며, 함수가 제공하지 않은 후보별 탈락 사유는 추측하지 않습니다.

검증: `python -m unittest discover -s tests`, `python tests/browser_smoke.py`, `python tests/browser_allocations.py` (브라우저 검증은 Playwright/Chromium 필요).
