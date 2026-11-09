"""Evidence presence in one MTK event directory; never search siblings.

Names follow aee.processor / mobilelog / bugreport producers, including the
correlated layout escape hatch. Presence does not certify archive integrity.
"""
from pathlib import Path

_SIDE_DIRS = {'mobilelog', 'correlated_mobilelogs', 'bugreport', 'correlated_bugreports'}


def event_evidence(event_dir: str | Path) -> dict[str, bool]:
    result = dict.fromkeys(('dblog', 'mobilelog', 'bugreport'), False)
    root = Path(event_dir)
    if root.is_symlink() or not root.is_dir():
        return result
    def files(directory):
        try:
            for p in directory.iterdir():
                if p.is_symlink():
                    continue
                if p.is_dir():
                    yield from files(p)
                elif p.is_file() and p.stat().st_size > 0:
                    yield p
        except OSError:
            return
    for p in files(root):
        parts = p.relative_to(root).parts
        if parts[0] in {'mobilelog', 'correlated_mobilelogs'}:
            if not p.name.endswith(('.tmp', '.part')):
                result['mobilelog'] = True
        elif parts[0] in {'bugreport', 'correlated_bugreports'}:
            if p.name.endswith('_bugreport.zip'):
                result['bugreport'] = True
        elif not set(parts[:-1]).intersection(_SIDE_DIRS) and p.suffix == '.dbg':
            result['dblog'] = True
    return result


def evidence_complete(evidence: dict) -> bool:
    return all(evidence.get(key) is True for key in ('dblog', 'mobilelog', 'bugreport'))
