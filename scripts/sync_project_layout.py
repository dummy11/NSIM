"""Keep the launcher's project discovery and CLI aliases identical to the core."""
import argparse
import ast
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    path = root / 'nsim-install.py'
    text = path.read_text()
    tree = ast.parse(text)
    changes = []
    for variable, filename in (('PROJECT_LAYOUT_SOURCE', 'project_paths.py'),
                               ('CLI_ALIASES_SOURCE', 'cli_aliases.py')):
        source = (root / 'src/nsim' / filename).read_text()
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and
                          any(isinstance(t, ast.Name) and t.id == variable for t in n.targets))
        if ast.literal_eval(assignment.value) != source:
            changes.append((assignment.lineno, assignment.end_lineno, variable, source))
    if not changes:
        print('project discovery and CLI alias embeddings are current')
        return 0
    if args.check:
        print('launcher embeddings are stale; run scripts/sync_project_layout.py')
        return 1
    lines = text.splitlines(keepends=True)
    for start, end, variable, source in sorted(changes, reverse=True):
        lines[start - 1:end] = [variable + ' = ' + repr(source) + '\n']
    path.write_text(''.join(lines))
    print('updated standalone launcher embeddings')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
