"""Copy packaged native dependencies from the matching installed FMPy release.

Run before editable installation, using an environment with fmpy==0.3.32 installed.
Only binary files are copied; repository Python sources are preserved.
"""
from importlib.metadata import distribution
from pathlib import Path
import shutil

dist = distribution("fmpy")
if dist.version != "0.3.32":
    raise SystemExit("Install fmpy==0.3.32 first")
target = Path(__file__).resolve().parents[1] / "src"
count = 0
for entry in dist.files or []:
    if entry.parts[0] != "fmpy":
        continue
    source = Path(dist.locate_file(entry))
    if source.suffix not in (".dll", ".so", ".dylib", ".exe") and source.name != "server_tcp":
        continue
    destination = target / str(entry)
    if source.resolve() == destination.resolve():
        continue
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    count += 1
print(f"Prepared {count} native files")
