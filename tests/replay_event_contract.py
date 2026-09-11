"""Consumer-shape controls, including event kinds absent from the default run.

The all-kinds trace is a display-contract fixture, not claimed simulation output.
The caller separately restores and clicks an actual browser-run decision.
"""
from copy import deepcopy


def event_contract_fixture(page, fixture):
    result = fixture['result']
    original = {event['kind']: event for event in result['events']}
    decision = original['decision']
    machine = result['model']['machines'][0]['id']
    route = result['model']['routes'][0]['id']
    required = {
        'arrival': [], 'ready': [], 'order_release': [], 'blocked': [],
        'decision': ['candidates', 'checks', 'decision_mode', 'reason'],
        'assigned': ['route', 'assignment_kind'], 'route_preference': ['route'],
        'move': ['route'], 'complete': ['route'],
        'machine_state': ['machine', 'transition'], 'transport_arrive': ['machine'],
        'start': ['machine', 'duration'], 'finish': ['machine'],
        'buffer_enter': ['buffer'], 'buffer_wait': ['buffer'],
        'setup_plan': ['machine', 'duration', 'family'],
        'setup_complete': ['machine', 'duration', 'family'],
        'resource_wait': [], 'resource_acquire': [], 'resource_release': [],
    }
    events = []
    for kind, fields in required.items():
        event = deepcopy(original['arrival'])
        event.update(kind=kind, index=len(events), time=0, allocation_id=None,
                     state_changes={'lots': {}, 'machines': {}})
        values = dict(route=route, machine=machine, assignment_kind='queue', duration=1,
                      family='A', buffer='BUF_INPUT', transition=original['machine_state']['transition'],
                      candidates=decision['candidates'], checks=decision['checks'],
                      decision_mode='pull', reason='raw reason 복원 日本語')
        event.update({field: deepcopy(values[field]) for field in fields})
        events.append(event)
    machine_only = deepcopy(next(event for event in events if event['kind'] == 'machine_state'))
    machine_only.update(index=len(events), lot=None, affected_lot_id=None)
    events.append(machine_only)
    restored = deepcopy(fixture)
    restored['result'].update(events=events, allocations=[])
    restored.update(cursor=len(events), tab='events', resultsFinal=False, filters={})

    def accepts(saved):
        return page.evaluate('saved=>canRestorePublicReplay(saved,saved.source)', saved)

    assert accepts(restored), 'all current event kinds with minimal consumer fields'
    checked = 0
    for index, event in enumerate(events):
        # Required lot envelope for every kind except a genuine machine-only
        # transition; optional decision evidence stays absent for other kinds.
        bad = deepcopy(restored);bad['result']['events'][index]['lot'] = None
        assert accepts(bad) == (event['kind'] == 'machine_state'), event['kind']
        checked += 1
        for field in required[event['kind']]:
            bad = deepcopy(restored);del bad['result']['events'][index][field]
            assert not accepts(bad), (event['kind'], field)
            checked += 1
        for field in ['checks', 'candidates']:
            bad = deepcopy(restored);bad['result']['events'][index][field] = [None]
            assert not accepts(bad), (event['kind'], field, 'null row')
            checked += 1
    for field in ['candidates', 'checks']:
        index = next(i for i, event in enumerate(events) if event['kind'] == 'decision')
        for value in [None, {}, [{}]]:
            bad = deepcopy(restored);bad['result']['events'][index][field] = value
            assert not accepts(bad), (field, value)
            checked += 1
    index = next(i for i, event in enumerate(events) if event['kind'] == 'machine_state')
    for value in [{}, {'previous': {}}, {'current': {}}, {'previous': None, 'current': None}]:
        bad = deepcopy(restored);bad['result']['events'][index]['transition'] = value
        assert not accepts(bad), value
        checked += 1
    bad = deepcopy(restored);bad['result']['events'][0]['state_changes']['machine_operations'] = {'UNKNOWN': {'state':'idle','lot':None,'since':0}}
    assert not accepts(bad), 'deferred Operations unknown machine'
    bad = deepcopy(restored);bad['result']['events'][0]['kind'] = 'unsupported_future_event'
    assert not accepts(bad), 'unsupported event must validate source instead'
    # True zero/empty arrays and optional localized metadata remain usable.
    good = deepcopy(restored)
    event = next(event for event in good['result']['events'] if event['kind'] == 'decision')
    event.update(candidates=[], checks=[], chosen=None, reason_message={'code':'message_25','args':[]})
    assert accepts(good), 'declined decision with empty arrays'
    for event in good['result']['events']:
        if 'duration' in event:event['duration'] = 0
    assert accepts(good), 'zero duration is structurally valid'
    print('PASS replay event-kind required/optional matrix', checked + 4, flush=True)
    return restored
