"""Compact retained samples and bounded JSON output for history collectors."""
from __future__ import print_function
import json

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


class HistorySample(object):
    """Read-only values with a shared field tuple instead of a per-row dict."""
    __slots__ = ('fields', 'values')

    def __init__(self, fields, values):
        self.fields = fields
        self.values = values

    def __getitem__(self, key):
        try:
            return self.values[self.fields.index(key)]
        except ValueError:
            raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return key in self.fields

    def as_dict(self):
        return dict(zip(self.fields, self.values))


def compact_sample_hook(required=('t', 'm5', 'a'), nested=()):
    schemas = {}
    strings = {}

    def pack(value):
        keys = tuple(sorted(value))
        if len(schemas) < 128:
            keys = schemas.setdefault(keys, keys)
        values = []
        for key in keys:
            item = value[key]
            if isinstance(item, string_types) and len(strings) < 256:
                item = strings.setdefault(item, item)
            elif key in nested and isinstance(item, dict):
                item = pack(item)
            values.append(item)
        return HistorySample(keys, tuple(values))

    def compact(value):
        return pack(value) if all(key in value for key in required) else value
    return compact


def encode_history_sample(value):
    if isinstance(value, HistorySample):
        return value.as_dict()
    raise TypeError('Cannot serialize %s' % type(value).__name__)


def write_history_json(handle, data, sort_keys=False, allow_nan=True):
    boards = data.get('boards') if isinstance(data, dict) else None
    kwargs = {'separators': (',', ':'), 'default': encode_history_sample,
              'sort_keys': sort_keys, 'allow_nan': allow_nan}
    if not isinstance(boards, list) or not all(
            isinstance(b, dict) and isinstance(b.get('samples'), list) for b in boards):
        handle.write(json.dumps(data, **kwargs))
        return
    # Keep the C encoder, but bound each output string while decoded history
    # remains live. Unknown metadata and sample fields round-trip unchanged.
    metadata = dict(data)
    del metadata['boards']
    handle.write(json.dumps(metadata, **kwargs)[:-1])
    handle.write(',' if metadata else '')
    handle.write('"boards":[')
    for index, board in enumerate(boards):
        if index:
            handle.write(',')
        metadata = dict(board)
        samples = metadata.pop('samples')
        handle.write(json.dumps(metadata, **kwargs)[:-1])
        handle.write(',' if metadata else '')
        handle.write('"samples":[')
        for start in range(0, len(samples), 128):
            if start:
                handle.write(',')
            handle.write(json.dumps(samples[start:start + 128], **kwargs)[1:-1])
        handle.write(']}')
    handle.write(']}')
