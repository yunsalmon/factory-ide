"""Deterministic wall-clock disruption plans, independent of routing randomness."""
import random
from model import ModelError
from messages import message


def compile_disruptions(model):
    plans = {}
    for machine in model['machines']:
        mid = machine['id']
        windows = [dict(w, source='calendar') for w in machine.get('availability', [])]
        windows += [dict(w, state='maintenance', cause=w.get('cause', 'planned_maintenance'), source='maintenance')
                    for w in machine.get('maintenance', [])]
        failures = machine.get('failures', [])
        if isinstance(failures, dict):
            rng = random.Random(f"{model['seed']}:{mid}:{failures.get('seed', 0)}:failures-v1")
            time, records = 0, []
            while True:
                time += rng.expovariate(1 / failures['mtbf'])
                if time > model['duration']:
                    break
                records.append(dict(start=time, repair_time=failures['repair_time']))
                if len(records) > 5000:
                    raise ModelError(message('ops_failure_limit', mid))
                time += failures['repair_time']
            failures, source = records, 'seeded_failure'
        else:
            source = 'deterministic_failure'
        for index, failure in enumerate(failures):
            windows.append(dict(start=failure['start'], end=failure['start'] + failure['repair_time'],
                                state='down', cause='failure_repair', source=source,
                                failure_id=f'{mid}-FAIL-{index + 1:04d}'))
        plans[mid] = sorted(windows, key=lambda w: (w['start'], w['end'], w['source']))
    return plans


class ResourcePool:
    """FIFO among available requesters, atomic bundles, no partial acquisition."""
    def __init__(self, factory):
        self.factory = factory
        self.definitions = {r['id']: dict(r) for r in factory.model.get('resources', [])}
        self.pending, self.held = [], {}
        self.serial = 0

    def snapshot(self):
        return {rid: dict(resource, holders=[dict(req, units=req['requirements'][rid])
                                             for req in self.held.values() if rid in req['requirements']],
                         waiters=[dict(req, units=req['requirements'][rid])
                                  for req in self.pending if rid in req['requirements']])
                for rid, resource in self.definitions.items()}

    def available_requester(self, request):
        mid = request['machine']
        return request['phase'] == 'transport' or not self.factory.unavailable(self.factory.machines[mid])

    def can_grant(self, request):
        # Independent bundles do not block each other; older overlapping bundles
        # get first claim. Unavailable machines cannot monopolize a queue head.
        if not self.available_requester(request):
            return False
        for older in self.pending:
            if older is request:
                break
            if self.available_requester(older) and set(older['requirements']) & set(request['requirements']):
                return False
        return all(sum(r['requirements'].get(rid, 0) for r in self.held.values()) + units <= self.definitions[rid]['capacity']
                   for rid, units in request['requirements'].items())

    def acquire(self, requirements, lot, machine, phase):
        if not requirements:
            return None
        self.serial += 1
        request = dict(id=f'REQ-{self.serial:06d}', requirements=dict(requirements), lot=lot['id'],
                       machine=machine, phase=phase, requested=self.factory.env.now)
        self.pending.append(request)
        if not self.can_grant(request):
            if machine:
                self.factory.set_stage(machine, 'resource_wait', lot, 'resource_contention')
            self.factory.emit('resource_wait', lot, machine=machine, allocation_id=None,
                              resource_request=request, reason='resource_contention')
        while not self.can_grant(request):
            yield self.factory.changed
        self.pending.remove(request)
        self.held[request['id']] = request
        if machine and phase != 'transport':
            self.factory.set_stage(machine, phase, lot, 'lot_setup' if phase == 'setup' else 'processing_started')
        self.factory.emit('resource_acquire', lot, machine=machine, allocation_id=None, resource_request=request)
        self.factory.wake()
        return request

    def release(self, request, cause='phase_complete', emit=True):
        if not request or self.held.pop(request['id'], None) is None:
            return
        if emit:
            self.factory.emit('resource_release', self.factory.lots[request['lot']], machine=request['machine'],
                              allocation_id=None, resource_request=request, reason=cause)
        self.factory.wake()

    def release_machine(self, mid):
        released = [request for request in self.held.values() if request['machine'] == mid and request['phase'] != 'transport']
        for request in released:
            self.release(request, cause='calendar_preemption', emit=False)
        return released

    def assert_capacity(self):
        for rid, resource in self.definitions.items():
            assert sum(r['requirements'].get(rid, 0) for r in self.held.values()) <= resource['capacity'], rid
