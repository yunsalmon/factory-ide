"""Exclusive operational intervals: one state per machine, through the horizon."""
STATES = ('idle', 'reserved', 'processing', 'setup', 'down', 'maintenance', 'blocked', 'starved', 'offshift', 'resource_wait')


def operation_metrics(result, cursor=None, final=True):
    events = result['events'] if cursor is None else result['events'][:cursor]
    horizon = result['model']['duration'] if final else (events[-1]['time'] if events else 0)
    current = {mid: dict(op) for mid, op in result['initial_state']['machine_operations'].items()}
    intervals, totals = [], {mid: {state: 0 for state in STATES} for mid in current}
    def close(mid, operation, end):
        start = operation['since']
        duration = max(0, end - start)
        if duration:
            intervals.append(dict(operation, machine=mid, start=start, end=end, duration=duration))
        totals[mid][operation['state']] += duration
    for event in events:
        if event['time'] > horizon: break
        for mid, operation in event['state_changes'].get('machine_operations', {}).items():
            close(mid, current[mid], event['time'])
            current[mid] = operation
    for mid, operation in current.items(): close(mid, operation, horizon)
    return dict(horizon=horizon, intervals=intervals, machines=totals)
