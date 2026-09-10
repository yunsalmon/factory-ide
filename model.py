"""Lossless MODEL-literal synchronization; never execute source during editing."""
import ast
import math
import pprint
import re


class ModelError(ValueError):
    pass


def number(value, label, low=0, high=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ModelError(f"{label}: {low}~{high} 범위의 숫자가 필요합니다.")


def validate(model):
    if not isinstance(model, dict):
        raise ModelError("MODEL은 딕셔너리여야 합니다.")
    for key in ('processes', 'machines', 'routes'):
        if not isinstance(model.get(key), list) or not model[key]:
            raise ModelError(f"{key}: 비어 있지 않은 목록이 필요합니다.")
    if len(model['machines']) > 100 or len(model['routes']) > 500:
        raise ModelError("로컬 실행 한도는 머신 100대, 경로 500개입니다.")
    if model.get('mode') not in ('pull', 'push'):
        raise ModelError("mode는 pull 또는 push입니다.")
    number(model.get('duration'), '실행 시간', .01, 100_000)
    number(model.get('seed'), 'seed', 0, 2**32 - 1)
    if not isinstance(model['seed'], int):
        raise ModelError('seed는 정수입니다.')
    source = model.get('source', {})
    number(source.get('count'), '로트 수', 1, 2000)
    if not isinstance(source['count'], int):
        raise ModelError('로트 수는 정수입니다.')
    number(source.get('interval'), '투입 간격', .01)
    products = source.get('products')
    if not isinstance(products, list) or not products or any(not isinstance(p, str) or not p.strip() for p in products):
        raise ModelError('제품 목록에는 하나 이상의 제품명이 필요합니다.')
    pids, mids, rids = set(), set(), set()
    def identifier(obj, used):
        value = obj.get('id')
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', value) or value in used or value in ('INPUT', 'OUTPUT'):
            raise ModelError(f'중복되거나 잘못된 ID: {value}. 영문으로 시작하는 영문·숫자·_·-를 사용하세요.')
        used.add(value)
        if not isinstance(obj.get('name', value), str):
            raise ModelError('이름은 문자열입니다.')
    for p in model['processes']:
        identifier(p, pids)
    for m in model['machines']:
        identifier(m, mids)
        if m.get('process') not in pids or not isinstance(m.get('line'), str) or not m['line'].strip():
            raise ModelError(f"{m['id']}: 공정과 라인을 확인하세요.")
        number(m.get('time'), f"{m['id']} 처리 시간", .01)
    for r in model['routes']:
        identifier(r, rids)
        if r.get('from') not in mids | {'INPUT'} or r.get('to') not in mids | {'OUTPUT'} or r['from'] == r['to'] or (r['from'] == 'INPUT' and r['to'] == 'OUTPUT'):
            raise ModelError(f"{r['id']}: 경로의 출발·도착 머신을 확인하세요.")
        number(r.get('delay'), f"{r['id']} 이동 시간")
        number(r.get('priority'), f"{r['id']} 우선순위", 0, 1000)
        if not isinstance(r.get('enabled'), bool) or not isinstance(r.get('product'), str):
            raise ModelError(f"{r['id']}: enabled는 bool, product는 문자열입니다.")
    # Cycles may represent rework, so retain them. Surface unreachable nodes as warnings.
    reachable = {'INPUT'}
    for _ in range(len(mids) + 1):
        reachable.update(r['to'] for r in model['routes'] if r['enabled'] and r['from'] in reachable)
    warnings = [f'{m}: INPUT에서 연결되지 않은 머신입니다.' for m in sorted(mids - reachable)]
    if 'OUTPUT' not in reachable:
        warnings.append('OUTPUT으로 연결된 경로가 없어 로트가 완료되지 않습니다.')
    return warnings


def parse(source):
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ModelError(f'{e.lineno}행: {e.msg}') from e
    nodes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and any(isinstance(t, ast.Name) and t.id == 'MODEL' for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    if len(nodes) != 1:
        raise ModelError('최상위 MODEL = {...} 정의가 정확히 하나 필요합니다.')
    node = nodes[0].value
    try:
        model = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as e:
        raise ModelError('양방향 편집을 위해 MODEL에는 Python 리터럴만 사용하세요. 동작은 아래 함수에서 구현합니다.') from e
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
