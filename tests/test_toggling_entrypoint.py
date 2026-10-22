from pathlib import Path
import subprocess
import sys


def test_runpy_entrypoint_resolves_siblings_in_a_clean_process(tmp_path):
    # The frozen analysis runner executes run_path, which does not add the script
    # directory to sys.path. An earlier import in the GUI must not mask this.
    script = Path(__file__).resolve().parents[1] / 'analysis_scripts/toggling_auswertung.py'
    result = subprocess.run([sys.executable, '-I', '-c',
                             'import runpy,sys; runpy.run_path(sys.argv[1], run_name="analysis_entry_test")',
                             str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
