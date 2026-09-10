"""Factory Studio · 두 라인의 가공 → 검사 → 포장 예제

MODEL은 공정도와 양방향 동기화됩니다. 아래 Python 함수는 화면 편집 시 보존됩니다.
시간 단위: 분. 제품 A/B 로트를 통째로 처리합니다.
"""

MODEL = {
    'name': '두 라인의 유연 생산 공장',
    'mode': 'pull',
    'duration': 180,
    'seed': 42,
    'source': {'count': 30, 'interval': 3, 'products': ['A', 'B']},
    'processes': [
        {'id': 'cut', 'name': '가공'},
        {'id': 'inspect', 'name': '검사'},
        {'id': 'pack', 'name': '포장'},
    ],
    'machines': [
        {'id': 'CUT_A', 'name': '가공기 A', 'process': 'cut', 'line': 'A', 'time': 7},
        {'id': 'CUT_B', 'name': '가공기 B', 'process': 'cut', 'line': 'B', 'time': 9},
        {'id': 'QC_A', 'name': '검사기 A', 'process': 'inspect', 'line': 'A', 'time': 6},
        {'id': 'QC_B', 'name': '검사기 B', 'process': 'inspect', 'line': 'B', 'time': 8},
        {'id': 'PACK_A', 'name': '포장기 A', 'process': 'pack', 'line': 'A', 'time': 5},
        {'id': 'PACK_B', 'name': '포장기 B', 'process': 'pack', 'line': 'B', 'time': 5},
    ],
    'routes': [
        {'id': 'r1', 'from': 'INPUT', 'to': 'CUT_A', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r2', 'from': 'INPUT', 'to': 'CUT_B', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r3', 'from': 'CUT_A', 'to': 'QC_A', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r4', 'from': 'CUT_B', 'to': 'QC_B', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r5', 'from': 'CUT_A', 'to': 'QC_B', 'priority': 10, 'delay': 2, 'product': '*', 'enabled': True},
        {'id': 'r6', 'from': 'CUT_B', 'to': 'QC_A', 'priority': 10, 'delay': 2, 'product': '*', 'enabled': True},
        {'id': 'r7', 'from': 'QC_A', 'to': 'PACK_A', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r8', 'from': 'QC_B', 'to': 'PACK_B', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r9', 'from': 'PACK_A', 'to': 'OUTPUT', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
        {'id': 'r10', 'from': 'PACK_B', 'to': 'OUTPUT', 'priority': 0, 'delay': 1, 'product': '*', 'enabled': True},
    ],
}


def choose_candidate(candidates, context):
    """Pull: 머신이 로트를 선택. Push: 로트가 목적지를 선택.

    candidates: id, lot_id, route_id, priority, ready_since,
                from, to, product, queue_length, same_line
    context: now, mode, machine (Pull), lot (Push), rng
    반환: (선택한 후보 id, 선택 이유 문자열)
    연결되지 않거나 제품 조건에 맞지 않는 후보는 전달되지 않습니다.
    """
    chosen = min(candidates, key=lambda c: (
        c['priority'], c['queue_length'], c['ready_since'], c['id']
    ))
    from messages import message
    return chosen['id'], message('message_25')


def processing_time(machine, lot, context):
    """양수 분 단위 반환. context['rng']로 재현 가능한 난수를 사용합니다."""
    return machine['time']


# 상세 SimPy 동작이 필요하면 아래 함수를 정의하세요.
# process_lot이 있으면 processing_time 대신 호출됩니다.
# context['shared']에 공용 simpy.Resource 등을 보관할 수 있습니다.
#
# def process_lot(env, machine, lot, context):
#     yield env.timeout(1)  # 준비 작업
#     yield env.timeout(machine['time'])  # 가공
