"""Structured built-in messages, retaining the historical string Python contract."""
import json
import re
from pathlib import Path
_CATALOG = json.loads((Path(__file__).parent / 'web/locales.json').read_text())['ko']

class Message(str):
    def __new__(cls, code, *args):
        value = super().__new__(cls, re.sub(r'\{(\d+)\}', lambda m: str(args[int(m[1])]), _CATALOG[code]))
        value.code, value.arguments = code, args
        return value
    def __reduce__(self):
        return (Message, (self.code, *self.arguments))

def message(code, *args):
    return Message(code, *args)

def descriptor(value):
    if isinstance(value, Message):
        return {'code': value.code, 'args': [descriptor(a) or a for a in value.arguments]}
    return None
