"""Development preview: restart the GUI after source/UI/style edits. No dependencies."""
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def snapshot():
    paths = [*ROOT.glob('*.py'), ROOT / 'style.qss', *ROOT.glob('*.ui'), *ROOT.glob('assets/*.svg')]
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in paths if p.is_file()}


def launch():
    return subprocess.Popen([sys.executable, str(ROOT / 'main.py'), *sys.argv[1:]], cwd=ROOT)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    print('MOXA live preview: save main.py, a .ui file or style.qss to reload.', flush=True)
    print('Reload resets simulated session data. Close the app or press Ctrl+C to stop.', flush=True)
    previous = snapshot()
    child = launch()
    changed_at = None
    try:
        while True:
            current = snapshot()
            if current != previous:
                previous = current
                changed_at = time.monotonic()
            if changed_at is not None and time.monotonic() - changed_at >= 0.6:
                print('Files changed — restarting preview...', flush=True)
                stop(child)
                child = launch()
                changed_at = None
            elif child.poll() is not None and changed_at is None:
                return child.returncode
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        stop(child)


if __name__ == '__main__':
    raise SystemExit(main())
