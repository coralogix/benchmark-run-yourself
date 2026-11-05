"""
py2/py3 compatibility (2018). We've been on py3 since 2019; six is still a
dependency because of this file and legacy/feeds.py.
"""
import six

string_types = six.string_types
text_type = six.text_type
iteritems = six.iteritems


def ensure_text(s, encoding="utf-8"):
    if isinstance(s, bytes):
        return s.decode(encoding)
    return six.text_type(s)


def ensure_str(s):
    return six.ensure_str(s) if s is not None else s


def is_string(x):
    return isinstance(x, six.string_types)
