"""Deterministic SimPy factory runtime with auditable routing decisions."""
from messages import message, descriptor
import copy
import random
import simpy
from model import ModelError, number, parse, validate, migrate_model


class Factory:
    def __init__(self, model, choose=None, timing=None, process=None):
        validate(model)
        self.model = migrate_model(model)
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
        self.last_lots = {}
        self.buffers = {b['id']: dict(b, contents=[]) for b in self.model['buffers']}
        self.buffer_at = {b['at']: b['id'] for b in self.model['buffers']}
        self.stages = {mid: dict(state='idle', lot=None, cause='initialized') for mid in self.machines}
        self.calendar = {mid: None for mid in self.machines}
        self.operations = {mid: dict(state='idle', lot=None, since=0, cause='initialized') for mid in self.machines}
        self.initial_state = dict(lots={}, machines={mid: dict(state='idle', lot=None) for mid in self.machines},
                                  buffers=copy.deepcopy(self.buffers), machine_operations=copy.deepcopy(self.operations))
        self.last_buffers = copy.deepcopy(self.buffers)
        self.last_operations = copy.deepcopy(self.operations)
        self.source_pending = None
        if process and any(m.get('availability') for m in self.machines.values()):
            raise ModelError('Custom process_lot cannot be combined with availability windows; use processing_time for pausable work')

    @staticmethod
    def default_choose(candidates, context):
        c = min(candidates, key=lambda c: (c['priority'], c['queue_length'], c['ready_since'], c['id']))
        return c['id'], message('message_25')

    def emit(self, kind, lot=None, **extra):
        if len(self.events) >= 50000:
            raise ModelError(message('message_26'))
        affected = lot
        # Schema-v2 readers expect an existing lot envelope. Machine-only events
        # use a context lot but never create a synthetic inventory item.
        lot = lot or next(iter(self.lots.values()), None)
        if lot is None:
            return
        machines = {}
        for mid, lid in self.busy.items():
            state = 'processing' if self.operations[mid]['state'] == 'processing' else 'reserved' if lid else 'idle'
            value = dict(state=state, lot=lid)
            if self.last_machines.get(mid) != value:
                machines[mid] = value
        lots = {lid: value for lid, value in self.lots.items() if self.last_lots.get(lid) != value}
        buffers = {bid: b for bid, b in self.buffers.items() if self.last_buffers.get(bid) != b}
        operations = {mid: op for mid, op in self.operations.items() if self.last_operations.get(mid) != op}
        self.last_lots.update(copy.deepcopy(lots))
        self.last_machines.update(copy.deepcopy(machines))
        self.last_buffers.update(copy.deepcopy(buffers))
        self.last_operations.update(copy.deepcopy(operations))
        if descriptor(extra.get('reason')):
            extra['reason_message'] = descriptor(extra['reason'])
        self.assert_inventory()
        self.events.append(copy.deepcopy(dict(index=len(self.events), time=self.env.now,
            kind=kind, lot=lot, affected_lot_id=affected['id'] if affected else None,
            allocation_id=extra.pop('allocation_id', affected.get('allocation_id') if affected else None),
            state_changes=dict(lots=lots, machines=machines,
                               buffers=buffers, machine_operations=operations), **extra)))

    def assert_inventory(self):
        members = {}
        for bid, b in self.buffers.items():
            if b['capacity'] is not None and len(b['contents']) > b['capacity']:
                raise AssertionError(f'Buffer overflow: {bid}')
            for lid in b['contents']:
                if lid in members:
                    raise AssertionError(f'Duplicate buffer lot: {lid}')
                members[lid] = bid
        for lid, lot in self.lots.items():
            place = lot['placement']
            if place['kind'] == 'buffer':
                assert members.get(lid) == place['id'], (lid, place)
            else:
                assert lid not in members, (lid, place)
                if place['kind'] == 'machine':
                    assert self.busy[place['id']] == lid, (lid, place)
                else:
                    assert place['kind'] == 'transport', (lid, place)
        assert set(members) <= set(self.lots)
        claims = [lid for lid in self.busy.values() if lid]
        assert len(claims) == len(set(claims)) and set(claims) <= set(self.lots)

    def has_space(self, bid):
        b = self.buffers[bid]
        return b['capacity'] is None or len(b['contents']) < b['capacity']

    def enqueue(self, lot, bid):
        assert self.has_space(bid)
        self.buffers[bid]['contents'].append(lot['id'])
        lot['placement'] = dict(kind='buffer', id=bid)
        self.sort_buffer(bid)

    def sort_buffer(self, bid):
        b = self.buffers[bid]
        if b['policy'] == 'priority':
            b['contents'].sort(key=lambda lid: (self.lots[lid]['priority'], self.lots[lid]['ready_since'], lid))

    def depart(self, lot, route):
        bid = lot['placement']['id']
        self.buffers[bid]['contents'].remove(lot['id'])
        lot['placement'] = dict(kind='transport', id=route['id'])
        self.wake()

    def set_stage(self, mid, state, lot=None, cause=None):
        self.stages[mid] = dict(state=state, lot=lot['id'] if lot else None, cause=cause or state)
        self.update_operation(mid)

    def update_operation(self, mid):
        stage = self.stages[mid]
        window = self.unavailable(self.machines[mid])
        state = window['state'] if window else stage['state']
        cause = window.get('cause', state) if window else stage['cause']
        lid = self.busy[mid]
        previous = self.operations[mid]
        value = dict(state=state, lot=lid, cause=cause)
        if all(previous[k] == value[k] for k in value):
            return
        lot = self.lots.get(lid)
        if lot and lot['placement'] == dict(kind='machine', id=mid):
            lot['state'] = state if state in ('processing', 'setup', 'down', 'offshift', 'blocked') else 'waiting'
        self.operations[mid] = dict(value, since=self.env.now)
        self.emit('machine_state', lot, machine=mid, allocation_id=None,
                  transition=dict(previous=dict(previous, end=self.env.now, duration=self.env.now - previous['since']),
                                  current=self.operations[mid]))

    def machine_calendar(self, machine):
        mid = machine['id']
        for window in machine.get('availability', []):
            yield self.env.timeout(window['start'] - self.env.now)
            self.calendar[mid] = window
            self.update_operation(mid)
            self.wake()
            yield self.env.timeout(window['end'] - self.env.now)
            self.calendar[mid] = None
            self.update_operation(mid)
            self.wake()

    def unavailable(self, machine):
        # Consult intervals as well as calendar callbacks: same-time timeout
        # ordering cannot allow work at a half-open unavailable boundary.
        return next((w for w in machine.get('availability', []) if w['start'] <= self.env.now < w['end']), None)

    def wait_available(self, machine):
        while self.unavailable(machine):
            yield self.env.timeout(self.unavailable(machine)['end'] - self.env.now)

    def active_delay(self, machine, duration):
        remaining = duration
        while remaining > 0:
            yield from self.wait_available(machine)
            next_start = min((w['start'] for w in machine.get('availability', []) if w['start'] > self.env.now), default=float('inf'))
            elapsed = min(remaining, next_start - self.env.now)
            yield self.env.timeout(elapsed)
            remaining -= elapsed

    def eligible_queue_heads(self, candidates):
        heads = {}
        for candidate in candidates:
            lot = self.lots[candidate['lot_id']]
            bid = lot['placement']['id']
            buffer = self.buffers[bid]
            if buffer['generated']:
                continue  # Migrated Pull models retain their custom chooser semantics.
            rank = buffer['contents'].index(lot['id'])
            heads[bid] = min(heads.get(bid, rank), rank)
        return [c for c in candidates if self.buffers[self.lots[c['lot_id']]['placement']['id']]['generated'] or
                self.buffers[self.lots[c['lot_id']]['placement']['id']]['contents'].index(c['lot_id']) == heads[self.lots[c['lot_id']]['placement']['id']]]

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
                reason = message('message_27')
            elif r['product'] not in ('*', lot['product']):
                reason = message('message_28' ,r['product'])
            elif lot.get('preferred_route') and lot['preferred_route'] != r['id']:
                reason = message('message_29' ,lot['preferred_route'])
            elif lot.get('target') and lot['target'] != r['to']:
                reason = message('message_30' ,lot['target'])
            checks.append(dict(route_id=r['id'], lot_id=lot['id'], to=r['to'], eligible=reason is None, reason=reason or message('message_31'), reason_message=descriptor(reason or message('message_31'))))
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
                               buffer_id=lot['placement']['id'], lot_priority=lot['priority'],
                               buffer_rank=self.buffers[lot['placement']['id']]['contents'].index(lot['id']),
                               product=lot['product'], **{'from': r['from'], 'to': r['to']},
                               same_line=bool(origin and destination and origin['line'] == destination['line']),
                               queue_length=sum(l['state'] != 'completed' and l.get('target') == r['to'] for l in self.lots.values()) + int(bool(self.busy.get(r['to'])))))
        return result, checks

    def decide(self, candidates, checks, context, lot=None, output_choice=False):
        selected, reason = self.choose(copy.deepcopy(candidates), dict(context, now=self.env.now, mode=self.model['mode'], rng=self.rng))
        chosen = next((c for c in candidates if c['id'] == selected), None)
        if (selected is not None and chosen is None) or not isinstance(reason, str) or not reason.strip():
            raise ModelError(message('message_32'))
        lot = lot or self.lots[(chosen or candidates[0])['lot_id']]
        preference = bool(output_choice and chosen and chosen['to'] != 'OUTPUT')
        allocation_id = f'ALLOC-{len(self.allocations) + 1:06d}' if chosen and not preference else None
        self.emit('decision', lot, chosen=chosen['id'] if chosen else None, candidates=candidates, checks=checks,
                  reason=reason, machine=context.get('machine', {}).get('id'), route=chosen['route_id'] if chosen else None,
                  decision_mode=self.model['mode'], allocation_id=allocation_id,
                  outcome='route_preference' if preference else 'selected' if chosen else 'declined')
        if not chosen:
            return None, lot
        if preference:
            lot['preferred_route'] = chosen['route_id']
            self.emit('route_preference', lot, allocation_id=None, route=chosen['route_id'],
                      reason=message('message_33'))
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
            self.set_stage(route['to'], 'reserved', lot, 'pull_allocation')
        self.emit('assigned', lot, route=route['id'],
                  assignment_kind='reservation' if reservation else 'shipment' if route['to'] == 'OUTPUT' else 'queue')
        self.allocations[-1]['after_cursor'] = len(self.events)

    def ready(self, lot):
        lot.update(state='waiting', ready_since=self.env.now, target=None, route=None, assigned_route=None, allocation_id=None, preferred_route=None)
        self.emit('ready', lot)
        candidates, checks = self.candidates(lot)
        if not candidates:
            self.emit('blocked', lot, checks=checks, reason=message('message_34'))
        elif self.model['mode'] == 'push' or any(c['to'] == 'OUTPUT' for c in candidates):
            route, _ = self.decide(candidates, checks, {'lot': copy.deepcopy(lot)}, lot,
                                   output_choice=self.model['mode'] == 'pull')
            if route is None:
                self.wake()
                return
            self.assign(lot, route)
            if route['to'] == 'OUTPUT':
                self.env.process(self.ship(lot, route))
        self.wake()

    def supply(self):
        source = self.model['source']
        scheduled = 0
        for i in range(source['count']):
            if i:
                scheduled += source['interval']
            if scheduled > self.env.now:
                yield self.env.timeout(scheduled - self.env.now)
            bid = self.buffer_at['INPUT']
            while not self.has_space(bid):
                self.source_pending = dict(index=i, scheduled=scheduled, buffer=bid)
                yield self.changed
            self.source_pending = None
            priorities = source.get('priorities', [0])
            lot = dict(id=f'LOT-{i + 1:03d}', product=source['products'][i % len(source['products'])],
                       priority=priorities[i % len(priorities)], scheduled=scheduled,
                       location='INPUT', state='waiting', created=self.env.now, ready_since=self.env.now,
                       target=None, route=None)
            self.lots[lot['id']] = lot
            self.enqueue(lot, bid)
            self.emit('arrival', lot)
            self.ready(lot)

    def ship(self, lot, route):
        self.depart(lot, route)
        lot.update(state='moving', target='OUTPUT', route=route['id'])
        self.emit('move', lot, route=route['id'], machine=None)
        yield self.env.timeout(route['delay'])
        bid = self.buffer_at['OUTPUT']
        if not self.has_space(bid):
            lot['state'] = 'blocked'
            self.emit('buffer_wait', lot, buffer=bid, reason='output_storage_full', allocation_id=None)
        while not self.has_space(bid):
            yield self.changed
        lot.update(state='completed', location='OUTPUT', target=None, completed=self.env.now)
        self.enqueue(lot, bid)
        self.emit('complete', lot, route=route['id'])
        self.wake()

    def machine(self, machine):
        mid = machine['id']
        while True:
            yield from self.wait_available(machine)
            candidates, checks = [], []
            for lot in self.lots.values():
                if lot['state'] != 'waiting' or lot['placement']['kind'] != 'buffer':
                    continue
                cs, rs = self.candidates(lot, mid)
                if self.model['mode'] == 'push':
                    cs = [c for c in cs if c['route_id'] == lot.get('assigned_route')]
                candidates.extend(cs)
                checks.extend(rs)
            heads = self.eligible_queue_heads(candidates)
            excluded = {c['id'] for c in candidates} - {c['id'] for c in heads}
            for check in checks:
                if f"{check['lot_id']}:{check['route_id']}" in excluded:
                    check.update(eligible=False, reason='buffer_queue_order', reason_message=None)
            candidates = heads
            if not candidates:
                self.set_stage(mid, 'starved', cause='no_eligible_input')
                yield self.changed
                continue
            if self.model['mode'] == 'pull':
                route, lot = self.decide(candidates, checks, {'machine': copy.deepcopy(machine)})
                if route is None:
                    self.set_stage(mid, 'idle', cause='selection_declined')
                    yield self.changed
                    continue
                self.assign(lot, route, reservation=True)
            else:
                selected = min(candidates, key=lambda c: (self.lots[c['lot_id']]['priority'] if self.buffers[self.lots[c['lot_id']]['placement']['id']]['policy'] == 'priority' else 0, c['ready_since'], c['id']))
                lot = self.lots[selected['lot_id']]
                route = next(r for r in self.model['routes'] if r['id'] == selected['route_id'])
            # Claim synchronously, before yielding: a lot cannot be claimed twice.
            self.busy[mid] = lot['id']
            self.set_stage(mid, 'reserved', lot, 'transport_reservation')
            self.depart(lot, route)
            lot.update(state='moving', target=mid, route=route['id'])
            self.emit('move', lot, route=route['id'], machine=mid)
            yield self.env.timeout(route['delay'])
            lot['placement'] = dict(kind='machine', id=mid)
            lot['location'] = mid
            lot['state'] = 'waiting'
            self.emit('transport_arrive', lot, machine=mid, allocation_id=None)
            yield from self.wait_available(machine)
            if machine.get('setup_time', 0):
                self.set_stage(mid, 'setup', lot, 'lot_setup')
                yield from self.active_delay(machine, machine['setup_time'])
            yield from self.wait_available(machine)
            context = {'now': self.env.now, 'rng': self.rng, 'shared': self.shared}
            duration = None if self.custom_process else self.timing(copy.deepcopy(machine), copy.deepcopy(lot), context)
            if duration is not None:
                number(duration, message('message_35' ,mid), .000001)
            lot.update(state='processing', location=mid, target=mid)
            self.set_stage(mid, 'processing', lot, 'processing_started')
            self.emit('start', lot, machine=mid, duration=duration)
            if self.custom_process:
                yield self.env.process(self.custom_process(self.env, copy.deepcopy(machine), copy.deepcopy(lot), context))
            else:
                yield from self.active_delay(machine, duration)
            bid = self.buffer_at[mid]
            lot['state'] = 'blocked' if not self.has_space(bid) else 'waiting'
            self.set_stage(mid, 'blocked' if not self.has_space(bid) else 'reserved', lot,
                           f'buffer_full:{bid}' if not self.has_space(bid) else 'processing_finished')
            self.emit('finish', lot, machine=mid)
            while not self.has_space(bid):
                yield self.changed
            lot['state'] = 'waiting'
            self.enqueue(lot, bid)
            self.busy[mid] = None
            self.set_stage(mid, 'idle', cause='output_released')
            self.emit('buffer_enter', lot, buffer=bid, allocation_id=None)
            self.ready(lot)

    def diagnose(self):
        incomplete = [lot for lot in self.lots.values() if lot['state'] != 'completed']
        if not incomplete and not self.source_pending:
            return []
        halted = self.env.peek() == float('inf')
        diagnostics = []
        for mid, op in self.operations.items():
            if op['state'] in ('blocked', 'starved', 'down', 'offshift'):
                related = [b for b in self.buffers.values() if mid in b['upstream'] or mid in b['downstream']]
                diagnostics.append(dict(code='deadlock' if halted else 'horizon_wait', machine=mid,
                    state=op['state'], cause=op['cause'], lot=op['lot'],
                    buffers=[b['id'] for b in related], lots=sorted({lid for b in related for lid in b['contents']} | ({op['lot']} if op['lot'] else set()))))
        for lot in incomplete:
            if lot['state'] == 'blocked' and lot['placement']['kind'] == 'transport':
                diagnostics.append(dict(code='deadlock' if halted else 'horizon_wait', machine=None,
                    buffers=[self.buffer_at['OUTPUT']], lots=[lot['id']], cause='output_storage_full'))
        if self.source_pending:
            diagnostics.append(dict(code='source_backpressure', **self.source_pending))
        return diagnostics

    def run(self):
        self.env.process(self.supply())
        for machine in self.machines.values():
            self.env.process(self.machine_calendar(machine))
            self.env.process(self.machine(machine))
        # SimPy's numeric until is exclusive. Step to include events at the horizon.
        while self.env.peek() <= self.model['duration']:
            self.env.step()
        completed = [l for l in self.lots.values() if l['state'] == 'completed']
        warnings = validate(self.model)
        if len(completed) < len(self.lots):
            warnings.append(message('message_36' ,len(self.lots) - len(completed)))
        return dict(schema_version=2, operational_schema_version=1, initial_state=self.initial_state,
                    operational_diagnostics=self.diagnose(), allocations=self.allocations, events=self.events, model=self.model, warnings=warnings, warning_messages=[descriptor(w) for w in warnings],
                    summary=dict(completed=len(completed), arrived=len(self.lots), horizon=self.model['duration'],
                                 mean_cycle_time=sum(l['completed'] - l['created'] for l in completed) / len(completed) if completed else 0))


def execute(source):
    model, _, _ = parse(source)
    namespace = {'__name__': '__factory_model__'}
    exec(compile(source, 'factory_model.py', 'exec'), namespace)
    if namespace.get('MODEL') != model:
        raise ModelError(message('message_37'))
    return Factory(model, namespace.get('choose_candidate'), namespace.get('processing_time'), namespace.get('process_lot')).run()
