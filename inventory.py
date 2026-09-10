"""Read-only inventory projection for schema-v2 traces, with or without operations."""
import copy
from model import migrate_model


def replay_inventory(result, cursor=None):
    """Consume exactly cursor events. Return detached legacy + explicit inventory.

    Keys lots/machines/queues/time remain useful to existing dashboards. New
    consumers use buffers, machine_operations and each lot's single placement.
    Historical v2 traces are projected through generated unbounded storage.
    """
    events = result['events']
    if cursor is None:
        cursor = len(events)
    if isinstance(cursor, bool) or not isinstance(cursor, int) or not 0 <= cursor <= len(events):
        raise ValueError('cursor must be an integer between zero and the event count')
    model = migrate_model(result['model'])
    initial = result.get('initial_state', {})
    state = copy.deepcopy(dict(
        lots=initial.get('lots', {}),
        machines=initial.get('machines', {m['id']: dict(state='idle', lot=None) for m in model['machines']}),
        buffers=initial.get('buffers', {b['id']: dict(b, contents=[]) for b in model['buffers']}),
        machine_operations=initial.get('machine_operations', {m['id']: dict(state='idle', lot=None, since=0, cause='legacy_projection') for m in model['machines']})))
    for event in events[:cursor]:
        changes = event.get('state_changes', {})
        for key in state:
            state[key].update(copy.deepcopy(changes.get(key, {})))
        if 'lots' not in changes and event.get('lot'):
            state['lots'][event['lot']['id']] = copy.deepcopy(event['lot'])
    at = {b['at']: b['id'] for b in model['buffers']}
    if not result.get('operational_schema_version'):
        for mid, value in state['machines'].items():
            state['machine_operations'][mid] = dict(value, since=None, cause='legacy_projection')
        for lot in state['lots'].values():
            if lot['state'] == 'processing':
                place = dict(kind='machine', id=lot['location'])
            elif lot['state'] == 'moving':
                place = dict(kind='transport', id=lot.get('route'))
            else:
                place = dict(kind='buffer', id=at[lot['location']])
                state['buffers'][place['id']]['contents'].append(lot['id'])
            lot['placement'] = place
            lot.setdefault('priority', 0)
    state['buffer_queues'] = {}
    claimed = {m['lot'] for m in state['machines'].values() if m['lot']}
    for bid, buffer in state['buffers'].items():
        buffer['occupancy'] = len(buffer['contents'])
        buffer['available_capacity'] = None if buffer['capacity'] is None else buffer['capacity'] - buffer['occupancy']
        state['buffer_queues'][bid] = [lid for lid in buffer['contents'] if state['lots'][lid]['state'] == 'waiting' and lid not in claimed]
    state['queues'] = {location: [lot['id'] for lot in sorted(state['lots'].values(), key=lambda l: (l['ready_since'], l['id']))
                                if lot['state'] == 'waiting' and lot['id'] not in claimed and (lot.get('target') or lot['location']) == location]
                       for location in at}
    state['time'] = events[cursor - 1]['time'] if cursor else 0
    state['cursor'] = cursor
    state['schema_version'] = 1
    state['summary'] = dict(arrived=len(state['lots']),
                            completed=sum(l['state'] == 'completed' for l in state['lots'].values()),
                            wip=sum(l['state'] != 'completed' for l in state['lots'].values()))
    return state
