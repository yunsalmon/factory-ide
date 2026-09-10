"""Deterministic SimPy factory runtime with auditable routing decisions."""
import copy
import random
import simpy
from model import ModelError, number, parse, validate


class Factory:
    def __init__(self, model, choose=None, timing=None, process=None):
        validate(model)
        self.model = copy.deepcopy(model)
        self.env = simpy.Environment()
        self.rng = random.Random(model['seed'])
        self.choose = choose or self.default_choose
        self.timing = timing or (lambda m, lot, ctx: m['time'])
        self.custom_process = process
        self.shared = {}
        self.machines = {m['id']: m for m in self.model['machines']}
        self.busy = {m: None for m in self.machines}
        self.lots = {}
        self.events = []
        self.changed = self.env.event()
        self.allocations = []
        self.last_machines = {}

    @staticmethod
    def default_choose(candidates, context):
        c = min(candidates, key=lambda c: (c['priority'], c['queue_length'], c['ready_since'], c['id']))
        return c['id'], '경로 우선순위 → 목적지 대기 수 → FIFO'

    def emit(self, kind, lot, **extra):
        if len(self.events) >= 50000:
            raise ModelError('이벤트 한도(50,000)를 초과했습니다. 실행 시간이나 로트 수를 줄이세요.')
        machines = {}
        for mid, lid in self.busy.items():
            state = 'processing' if lid and self.lots[lid]['state'] == 'processing' else 'reserved' if lid else 'idle'
            value = dict(state=state, lot=lid)
            if self.last_machines.get(mid) != value:
                machines[mid] = value
        self.last_machines.update(copy.deepcopy(machines))
        self.events.append(copy.deepcopy(dict(index=len(self.events), time=self.env.now,
            kind=kind, lot=lot, allocation_id=lot.get('allocation_id'),
            state_changes=dict(lots={lot['id']: lot}, machines=machines), **extra)))

    def wake(self):
        previous, self.changed = self.changed, self.env.event()
        previous.succeed()

    def route_checks(self, lot, target=None):
        checks = []
        for r in self.model['routes']:
            if r['from'] != lot['location'] or (target and r['to'] != target):
                continue
            reason = None
            if not r['enabled']:
                reason = '비활성 경로'
            elif r['product'] not in ('*', lot['product']):
                reason = f"제품 조건 불일치 ({r['product']})"
            elif lot.get('target') and lot['target'] != r['to']:
                reason = f"Push에서 {lot['target']}에 배정됨"
            checks.append(dict(route_id=r['id'], lot_id=lot['id'], to=r['to'], eligible=reason is None, reason=reason or '선택 가능'))
        return checks

    def candidates(self, lot, target=None):
        checks = self.route_checks(lot, target)
        eligible = {c['route_id'] for c in checks if c['eligible']}
        result = []
        for r in self.model['routes']:
            if r['id'] not in eligible:
                continue
            origin, destination = self.machines.get(r['from']), self.machines.get(r['to'])
            result.append(dict(id=f"{lot['id']}:{r['id']}", lot_id=lot['id'], route_id=r['id'],
                               priority=r['priority'], ready_since=lot['ready_since'],
                               product=lot['product'], **{'from': r['from'], 'to': r['to']},
                               same_line=bool(origin and destination and origin['line'] == destination['line']),
                               queue_length=sum(l['state'] != 'completed' and l.get('target') == r['to'] for l in self.lots.values()) + int(bool(self.busy.get(r['to'])))))
        return result, checks

    def decide(self, candidates, checks, context, lot=None):
        selected, reason = self.choose(copy.deepcopy(candidates), dict(context, now=self.env.now, mode=self.model['mode'], rng=self.rng))
        chosen = next((c for c in candidates if c['id'] == selected), None)
        if (selected is not None and chosen is None) or not isinstance(reason, str) or not reason.strip():
            raise ModelError('choose_candidate는 전달받은 후보 id와 비어 있지 않은 이유를 반환해야 합니다.')
        lot = lot or self.lots[(chosen or candidates[0])['lot_id']]
        allocation_id = f'ALLOC-{len(self.allocations) + 1:06d}' if chosen else None
        self.emit('decision', lot, chosen=chosen['id'] if chosen else None, candidates=candidates, checks=checks,
                  reason=reason, machine=context.get('machine', {}).get('id'), route=chosen['route_id'] if chosen else None,
                  decision_mode=self.model['mode'], proposed_allocation_id=allocation_id)
        if not chosen:
            return None, lot
        self.allocations.append(dict(id=allocation_id, lot_id=lot['id'], destination=chosen['to'],
            route_id=chosen['route_id'], mode=self.model['mode'], decision_index=len(self.events) - 1,
            before_cursor=len(self.events) - 1, after_cursor=None))
        # The decision records the new identity; the previous lot allocation is historical.
        self.events[-1]['allocation_id'] = allocation_id
        lot['allocation_id'] = allocation_id
        return next(r for r in self.model['routes'] if r['id'] == chosen['route_id']), lot

    def assign(self, lot, route, reservation=False):
        lot.update(target=route['to'], assigned_route=route['id'])
        if reservation:
            self.busy[route['to']] = lot['id']
        self.emit('assigned', lot, route=route['id'],
                  assignment_kind='reservation' if reservation else 'shipment' if route['to'] == 'OUTPUT' else 'queue')
        self.allocations[-1]['after_cursor'] = len(self.events)

    def ready(self, lot):
        lot.update(state='waiting', ready_since=self.env.now, target=None, route=None, assigned_route=None, allocation_id=None)
        self.emit('ready', lot)
        candidates, checks = self.candidates(lot)
        if not candidates:
            self.emit('blocked', lot, checks=checks, reason='선택 가능한 출고 경로가 없습니다.')
        elif self.model['mode'] == 'push' or any(c['to'] == 'OUTPUT' for c in candidates):
            route, _ = self.decide(candidates, checks, {'lot': copy.deepcopy(lot)}, lot)
            if route is None:
                self.wake()
                return
            self.assign(lot, route)
            if route['to'] == 'OUTPUT':
                self.env.process(self.ship(lot, route))
        self.wake()

    def supply(self):
        for i in range(self.model['source']['count']):
            if i:
                yield self.env.timeout(self.model['source']['interval'])
            lot = dict(id=f'LOT-{i + 1:03d}', product=self.model['source']['products'][i % len(self.model['source']['products'])],
                       location='INPUT', state='waiting', created=self.env.now, ready_since=self.env.now,
                       target=None, route=None)
            self.lots[lot['id']] = lot
            self.emit('arrival', lot)
            self.ready(lot)

    def ship(self, lot, route):
        lot.update(state='moving', target='OUTPUT', route=route['id'])
        self.emit('move', lot, route=route['id'], machine=None)
        yield self.env.timeout(route['delay'])
        lot.update(state='completed', location='OUTPUT', target=None, completed=self.env.now)
        self.emit('complete', lot, route=route['id'])
        self.wake()

    def machine(self, machine):
        mid = machine['id']
        while True:
            candidates, checks = [], []
            for lot in self.lots.values():
                if lot['state'] != 'waiting':
                    continue
                cs, rs = self.candidates(lot, mid)
                if self.model['mode'] == 'push':
                    cs = [c for c in cs if c['route_id'] == lot.get('assigned_route')]
                candidates.extend(cs)
                checks.extend(rs)
            if not candidates:
                yield self.changed
                continue
            if self.model['mode'] == 'pull':
                route, lot = self.decide(candidates, checks, {'machine': copy.deepcopy(machine)})
                if route is None:
                    yield self.changed
                    continue
                self.assign(lot, route, reservation=True)
            else:
                selected = min(candidates, key=lambda c: (c['ready_since'], c['id']))
                lot = self.lots[selected['lot_id']]
                route = next(r for r in self.model['routes'] if r['id'] == selected['route_id'])
            # Claim synchronously, before yielding: a lot cannot be claimed twice.
            self.busy[mid] = lot['id']
            lot.update(state='moving', target=mid, route=route['id'])
            self.emit('move', lot, route=route['id'], machine=mid)
            yield self.env.timeout(route['delay'])
            context = {'now': self.env.now, 'rng': self.rng, 'shared': self.shared}
            duration = None if self.custom_process else self.timing(copy.deepcopy(machine), copy.deepcopy(lot), context)
            if duration is not None:
                number(duration, f'{mid} processing_time 반환값', .000001)
            lot.update(state='processing', location=mid, target=mid)
            self.emit('start', lot, machine=mid, duration=duration)
            if self.custom_process:
                yield self.env.process(self.custom_process(self.env, copy.deepcopy(machine), copy.deepcopy(lot), context))
            else:
                yield self.env.timeout(duration)
            self.busy[mid] = None
            self.emit('finish', lot, machine=mid)
            self.ready(lot)

    def run(self):
        self.env.process(self.supply())
        for machine in self.machines.values():
            self.env.process(self.machine(machine))
        # SimPy's numeric until is exclusive. Step to include events at the horizon.
        while self.env.peek() <= self.model['duration']:
            self.env.step()
        completed = [l for l in self.lots.values() if l['state'] == 'completed']
        warnings = validate(self.model)
        if len(completed) < len(self.lots):
            warnings.append(f'{len(self.lots) - len(completed)}개 로트가 종료 시점에 미완료입니다. 대기·처리 중이거나 경로가 막혀 있을 수 있습니다.')
        return dict(schema_version=2, allocations=self.allocations, events=self.events, model=self.model, warnings=warnings,
                    summary=dict(completed=len(completed), arrived=len(self.lots), horizon=self.model['duration'],
                                 mean_cycle_time=sum(l['completed'] - l['created'] for l in completed) / len(completed) if completed else 0))


def execute(source):
    model, _, _ = parse(source)
    namespace = {'__name__': '__factory_model__'}
    exec(compile(source, 'factory_model.py', 'exec'), namespace)
    if namespace.get('MODEL') != model:
        raise ModelError('실행 중 MODEL 변경은 지원하지 않습니다. 선언부 또는 편집 화면에서 수정하세요.')
    return Factory(model, namespace.get('choose_candidate'), namespace.get('processing_time'), namespace.get('process_lot')).run()
