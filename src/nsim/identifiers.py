"""Readable run identifiers with bounded labels and full UUID uniqueness."""

from datetime import datetime
from pathlib import Path
import re
import uuid


def readable_id(label, fallback):
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    label = re.sub(r'[^A-Za-z0-9_.-]+', '-', label).strip('._-')[:48] or fallback
    return f'{label}_{stamp}_{uuid.uuid4().hex}'


def new_regression_id(caselists):
    """Name a regression after its ordered caselists, including saved replays."""
    return readable_id('-'.join(Path(path).stem for path in caselists), 'regr')
