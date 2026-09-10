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
