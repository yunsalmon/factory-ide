"""Versioned order plans: validate without modifying raw business values."""
import copy
import math
import re
from datetime import datetime
from messages import message


def normalize_orders(model):
    from model import ModelError

    def fail(field):
        raise ModelError(message('order_invalid', field))

    def numeric(value, field, low=0, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < low or (positive and value <= 0) or value > 1_000_000_000:
            fail(field)
        return value

    def timestamp(value, field):
        try:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})', value):
                fail(field)
            # datetime.fromisoformat normalizes +00:60; RFC3339 and JS reject it.
            if not value.endswith('Z') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
                fail(field)
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                fail(field)
            return parsed
        except (ValueError, OverflowError):
            fail(field)

    risk = numeric(model.get('order_risk_window', 30), 'order_risk_window')
    origin = timestamp(model['time_origin'], 'time_origin') if 'time_origin' in model else None
    orders = model.get('orders', [])
    if not isinstance(orders, list) or len(orders) > 2000:
        fail('orders')
    order_ids, lot_ids, result = set(), set(), []

    def identifier(value, used, field):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value) or value in used:
            fail(field)
        used.add(value)

    for raw in orders:
        if not isinstance(raw, dict):
            fail('orders[]')
        identifier(raw.get('id'), order_ids, 'order.id')
        oid = raw['id']
        if not isinstance(raw.get('product'), str) or not raw['product'].strip():
            fail(f'{oid}.product')
        qty = numeric(raw.get('quantity'), f'{oid}.quantity', positive=True)
        release = numeric(raw.get('release_time', 0), f'{oid}.release_time')
        numeric(raw.get('priority', 0), f'{oid}.priority', low=-1_000_000)
        due = raw.get('due_time')
        if due is not None:
            numeric(due, f'{oid}.due_time', low=-1_000_000_000)
        if raw.get('due_date') is not None:
            date = timestamp(raw['due_date'], f'{oid}.due_date')
            if origin is None:
                fail('time_origin / due_date')
            converted = (date - origin).total_seconds() / 60
            if due is not None and not math.isclose(due, converted, rel_tol=0, abs_tol=1e-7):
                fail(f'{oid}.due_time / due_date')
            due = converted
        for field in ['customer', 'reference']:
            if field in raw and not isinstance(raw[field], str):
                fail(f'{oid}.{field}')
        lots = raw.get('lots', [{'id': oid, 'quantity': qty}])
        if not isinstance(lots, list) or not lots:
            fail(f'{oid}.lots')
        total, plans = 0, []
        for lot in lots:
            if not isinstance(lot, dict):
                fail(f'{oid}.lots[]')
            identifier(lot.get('id'), lot_ids, f'{oid}.lot.id')
            quantity = numeric(lot.get('quantity'), f'{oid}.lot.quantity', positive=True)
            scheduled = numeric(lot.get('release_time', release), f'{oid}.lot.release_time')
            if scheduled < release:
                fail(f'{oid}.lot.release_time < order.release_time')
            total += quantity
            plans.append(dict(id=lot['id'], order_id=oid, product=raw['product'], quantity=quantity,
                              release_time=scheduled, due_time=due, due_date=raw.get('due_date'),
                              priority=raw.get('priority', 0), customer=raw.get('customer'), reference=raw.get('reference')))
        if min(lot['release_time'] for lot in plans) != release:
            fail(f'{oid}.first_lot.release_time')
        if not math.isclose(total, qty, rel_tol=1e-12, abs_tol=1e-9):
            raise ModelError(message('order_quantity_mismatch', oid, qty, total))
        if len(lot_ids) > 2000:
            fail('maximum 2000 order lots')
        result.append(dict(raw=copy.deepcopy(raw), id=oid, quantity=qty, release_time=release,
                           due_time=due, risk_window=risk, lots=plans))
    return result
