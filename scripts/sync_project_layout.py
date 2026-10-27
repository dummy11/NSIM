"""Keep the dependency-free launcher's project discovery identical to the core."""
import argparse
import ast
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    source = (root / 'src/nsim/project_paths.py').read_text()
    path = root / 'nsim-install.py'
    text = path.read_text()
    tree = ast.parse(text)
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and
                      any(isinstance(t, ast.Name) and t.id == 'PROJECT_LAYOUT_SOURCE' for t in n.targets))
    if ast.literal_eval(assignment.value) == source:
        print('project discovery embedding is current')
        return 0
    if args.check:
        print('project discovery embedding is stale; run scripts/sync_project_layout.py')
        return 1
    lines = text.splitlines(keepends=True)
    lines[assignment.lineno - 1:assignment.end_lineno] = ['PROJECT_LAYOUT_SOURCE = ' + repr(source) + '\n']
    path.write_text(''.join(lines))
    print('updated standalone project discovery')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
