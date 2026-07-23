import pathlib
import shutil

def main():
    root = pathlib.Path('.')
    for p in root.rglob('__pycache__'):
        if '.claude' not in p.parts:
            shutil.rmtree(p, ignore_errors=True)
    for p in root.rglob('*.pyc'):
        if p.is_file() and '.claude' not in p.parts:
            p.unlink()
    
    import os
    dirs_to_remove = ['.pytest_cache', '.mypy_cache', '.ruff_cache', 'build', 'dist', 'wheelhouse', 'bambu_cli.egg-info', 'bambu_local_cli.egg-info', 'platecli.egg-info']
    if not os.environ.get("GITHUB_ACTIONS"):
        dirs_to_remove.append('.venv')
    for name in dirs_to_remove:
        shutil.rmtree(root / name, ignore_errors=True)
        
    for p in root.glob('.bambu-download-*.zip'):
        if p.is_file():
            p.unlink()

if __name__ == '__main__':
    main()
