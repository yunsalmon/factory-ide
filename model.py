"""Lossless MODEL-literal synchronization; never execute source during editing."""
from messages import message, descriptor
import ast
import math
import pprint
import re


class ModelError(ValueError):
    def __init__(self, value):
        super().__init__(value)
        self.message = descriptor(value)


def number(value, label, low=0, high=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ModelError(message('message_1' ,label, low, high))


def validate(model):
    if not isinstance(model, dict):
        raise ModelError(message('message_2'))
    for key in ('processes', 'machines', 'routes'):
        if not isinstance(model.get(key), list) or not model[key]:
            raise ModelError(message('message_3' ,key))
    if len(model['machines']) > 100 or len(model['routes']) > 500:
        raise ModelError(message('message_4'))
    if model.get('mode') not in ('pull', 'push'):
        raise ModelError(message('message_5'))
    number(model.get('duration'), message('message_6'), .01, 100_000)
    number(model.get('seed'), 'seed', 0, 2**32 - 1)
    if not isinstance(model['seed'], int):
        raise ModelError(message('message_7'))
    source = model.get('source', {})
    number(source.get('count'), message('message_8'), 1, 2000)
    if not isinstance(source['count'], int):
        raise ModelError(message('message_9'))
    number(source.get('interval'), message('message_10'), .01)
    products = source.get('products')
    if not isinstance(products, list) or not products or any(not isinstance(p, str) or not p.strip() for p in products):
        raise ModelError(message('message_11'))
    pids, mids, rids = set(), set(), set()
    def identifier(obj, used):
        value = obj.get('id')
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', value) or value in used or value in ('INPUT', 'OUTPUT'):
            raise ModelError(message('message_12' ,value))
        used.add(value)
        if not isinstance(obj.get('name', value), str):
            raise ModelError(message('message_13'))
    for p in model['processes']:
        identifier(p, pids)
    for m in model['machines']:
        identifier(m, mids)
        if m.get('process') not in pids or not isinstance(m.get('line'), str) or not m['line'].strip():
            raise ModelError(message('message_14' ,m['id']))
        number(m.get('time'), message('message_15' ,m['id']), .01)
    for r in model['routes']:
        identifier(r, rids)
        if r.get('from') not in mids | {'INPUT'} or r.get('to') not in mids | {'OUTPUT'} or r['from'] == r['to'] or (r['from'] == 'INPUT' and r['to'] == 'OUTPUT'):
            raise ModelError(message('message_16' ,r['id']))
        number(r.get('delay'), message('message_17' ,r['id']))
        number(r.get('priority'), message('message_18' ,r['id']), 0, 1000)
        if not isinstance(r.get('enabled'), bool) or not isinstance(r.get('product'), str):
            raise ModelError(message('message_19' ,r['id']))
    validate_operations(model)
    validate_disruptions(model)
    # Cycles may represent rework, so retain them. Surface unreachable nodes as warnings.
    reachable = {'INPUT'}
    for _ in range(len(mids) + 1):
        reachable.update(r['to'] for r in model['routes'] if r['enabled'] and r['from'] in reachable)
    warnings = [message('message_20' ,m) for m in sorted(mids - reachable)]
    if 'OUTPUT' not in reachable:
        warnings.append(message('message_21'))
    return warnings


def parse(source):
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ModelError(message('message_22' ,e.lineno, e.msg)) from e
    nodes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and any(isinstance(t, ast.Name) and t.id == 'MODEL' for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    if len(nodes) != 1:
        raise ModelError(message('message_23'))
    node = nodes[0].value
    try:
        model = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as e:
        raise ModelError(message('message_24')) from e
    warnings = validate(model)
    return model, node, warnings


def synchronize(source, model):
    _, node, _ = parse(source)
    validate(model)
    # AST columns are UTF-8 byte offsets, not Python character indices.
    lines = source.encode('utf-8').splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1])) + node.col_offset
    end = sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset
    data = source.encode('utf-8')
    return (data[:start] + pprint.pformat(model, sort_dicts=False, width=88).encode() + data[end:]).decode('utf-8')


def validate_operations(model):
    """Validate the optional operational extension without rewriting source models."""
    mids = {m['id'] for m in model['machines']}
    buffers = model.get('buffers', [])
    if not isinstance(buffers, list):
        raise ModelError('buffers must be a list')
    ids, locations = set(), set()
    for b in buffers:
        if not isinstance(b, dict):
            raise ModelError('Each buffer must be an object')
        bid, at = b.get('id'), b.get('at')
        if not isinstance(bid, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', bid) or bid in ids:
            raise ModelError('Buffer IDs must be unique stable identifiers')
        if at not in mids | {'INPUT', 'OUTPUT'} or at in locations:
            raise ModelError('Each buffer must have a unique valid at location')
        ids.add(bid); locations.add(at)
        capacity = b.get('capacity')
        if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1):
            raise ModelError('Buffer capacity must be null (unbounded) or a positive integer')
        if b.get('policy', 'fifo') not in ('fifo', 'priority'):
            raise ModelError('Buffer policy must be fifo or priority')
    for m in model['machines']:
        number(m.get('setup_time', 0), f"{m['id']} setup_time")
        windows = m.get('availability', [])
        if not isinstance(windows, list):
            raise ModelError('Machine availability must be a list')
        end = -1
        for window in windows:
            if not isinstance(window, dict) or window.get('state') not in ('down', 'offshift'):
                raise ModelError('Availability states must be down or offshift')
            number(window.get('start'), 'Availability start')
            number(window.get('end'), 'Availability end')
            if window['start'] >= window['end'] or window['start'] < end:
                raise ModelError('Availability windows must be sorted, positive and non-overlapping')
            if not isinstance(window.get('cause', window['state']), str):
                raise ModelError('Availability cause must be text')
            end = window['end']
    priorities = model['source'].get('priorities', [0])
    if not isinstance(priorities, list) or not priorities:
        raise ModelError('Source priorities must be a non-empty list')
    for priority in priorities:
        number(priority, 'Lot priority', -1_000_000)


def migrate_model(model):
    """Copy-on-read migration: one explicit output storage per legacy location."""
    import copy
    validate(model)
    result = copy.deepcopy(model)
    supplied = {b['at']: b for b in result.get('buffers', [])}
    used = {b['id'] for b in supplied.values()}
    buffers = []
    for at in ['INPUT', *(m['id'] for m in result['machines']), 'OUTPUT']:
        if at in supplied:
            b = supplied[at]
            b.setdefault('capacity', None)
            b.setdefault('policy', 'fifo')
            b.setdefault('generated', False)
        else:
            bid = f'BUF_{at}'
            suffix = 1
            while bid in used:
                bid = f'BUF_{at}_{suffix}'; suffix += 1
            used.add(bid)
            b = dict(id=bid, at=at, capacity=None, policy='fifo', generated=True)
        b['upstream'] = [] if at == 'INPUT' else [at] if at != 'OUTPUT' else sorted({r['from'] for r in result['routes'] if r['to'] == 'OUTPUT'})
        b['downstream'] = sorted({r['to'] for r in result['routes'] if r['from'] == at})
        buffers.append(b)
    result['buffers'] = buffers
    result['operational_model_version'] = 1
    return result


def validate_disruptions(model):
    def invalid(field):
        raise ModelError(message('ops_invalid', field))
    resources = model.get('resources', [])
    if not isinstance(resources, list): invalid('resources')
    capacities = {}
    for resource in resources:
        if not isinstance(resource, dict): invalid('resources')
        rid = resource.get('id')
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', rid) or rid in capacities:
            invalid('resource.id')
        capacity = resource.get('capacity')
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 1000:
            invalid('resource.capacity')
        if resource.get('kind') not in ('operator', 'tool', 'transport'): invalid('resource.kind')
        if not isinstance(resource.get('name', rid), str): invalid('resource.name')
        capacities[rid] = capacity
    families = model.get('product_families', {})
    if not isinstance(families, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip() for k, v in families.items()):
        invalid('product_families')
    def requirements(value, field):
        if not isinstance(value, dict): invalid(field)
        for rid, units in value.items():
            if rid not in capacities or isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= capacities[rid]: invalid(field)
    for machine in model['machines']:
        if machine.get('initial_family') is not None and (not isinstance(machine['initial_family'], str) or not machine['initial_family'].strip()): invalid('initial_family')
        matrix = machine.get('setup_matrix', {})
        if not isinstance(matrix, dict): invalid('setup_matrix')
        for origin, destinations in matrix.items():
            if not isinstance(origin, str) or not origin.strip() or not isinstance(destinations, dict): invalid('setup_matrix')
            for target, duration in destinations.items():
                if not isinstance(target, str) or not target.strip(): invalid('setup_matrix')
                number(duration, 'setup_matrix.duration')
        demands = machine.get('resource_requirements', {})
        if not isinstance(demands, dict) or set(demands) - {'setup', 'processing'}: invalid('resource_requirements')
        for phase, value in demands.items(): requirements(value, f'resource_requirements.{phase}')
        maintenance = machine.get('maintenance', [])
        if not isinstance(maintenance, list): invalid('maintenance')
        for window in maintenance:
            if not isinstance(window, dict): invalid('maintenance')
            number(window.get('start'), 'maintenance.start'); number(window.get('end'), 'maintenance.end')
            if window['start'] >= window['end']: invalid('maintenance')
            if not isinstance(window.get('cause', 'planned_maintenance'), str): invalid('maintenance.cause')
        failures = machine.get('failures', [])
        if isinstance(failures, dict):
            number(failures.get('mtbf'), 'failures.mtbf', .01)
            number(failures.get('repair_time'), 'failures.repair_time', .01)
            seed = failures.get('seed', 0)
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1: invalid('failures.seed')
        elif isinstance(failures, list):
            end = -1
            for failure in failures:
                if not isinstance(failure, dict): invalid('failures')
                number(failure.get('start'), 'failure.start')
                number(failure.get('repair_time'), 'failure.repair_time', .01)
                if failure['start'] < end: invalid('failures.overlap')
                end = failure['start'] + failure['repair_time']
        else: invalid('failures')
    for route in model['routes']:
        requirements(route.get('resources', {}), 'route.resources')
