"""Validate the encrypted credential envelope; accept no plaintext fields."""
import base64
import re

def validate_auth(value):
    keys = {'version', 'salt', 'iterations', 'iv', 'data', 'feedback_repository', 'feedback_branch'}
    if not isinstance(value, dict) or set(value) != keys or value['version'] != 1:
        raise ValueError('Invalid encrypted credential envelope')
    if value['iterations'] != 600000:
        raise ValueError('Unsupported credential work factor')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value['feedback_repository']):
        raise ValueError('Invalid private repository reference')
    if value['feedback_repository'].lower() == 'barasch/observatory' or value['feedback_branch'] != 'main':
        raise ValueError('Feedback must use a separate private repository on main')
    for field, length in [('salt',16),('iv',12),('data',None)]:
        raw = base64.b64decode(value[field], validate=True)
        if (length and len(raw) != length) or (field == 'data' and not 32 <= len(raw) <= 4096):
            raise ValueError('Invalid encrypted credential encoding')
    return value
