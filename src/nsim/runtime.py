"""Launch NSIM workers from the controller's package, not a job's cwd/PYTHONPATH."""

from pathlib import Path


def module_command(python, module, *args):
    package_root = str(Path(__file__).resolve().parent.parent)
    script = ('import runpy,sys; sys.path.insert(0, ' + repr(package_root) + '); '
              'runpy.run_module(' + repr(module) + ", run_name='__main__', alter_sys=True)")
    return [python, '-I', '-c', script, *args]
