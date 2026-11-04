"""Prepare the official FMI 2.0 Feedthrough sample without changing its contents."""
import hashlib
from pathlib import Path
from urllib.request import urlopen
from io import BytesIO
from zipfile import ZipFile

root = Path(__file__).resolve().parents[2]
archive = root / "Reference-FMUs-0.0.39.zip"
checksum = "6863d55e5818e1ca4e4614c4d4ba4047a921b4495f6336e7002874ed791f6c2a"
url = "https://github.com/modelica/Reference-FMUs/releases/download/v0.0.39/Reference-FMUs-0.0.39.zip"
if archive.is_file():
    data = archive.read_bytes()
else:
    with urlopen(url, timeout=60) as response:
        data = response.read()
if hashlib.sha256(data).hexdigest() != checksum:
    raise SystemExit("Reference FMUs checksum mismatch")
with ZipFile(BytesIO(data)) as bundle:
    fmu = bundle.read("2.0/Feedthrough.fmu")
target = Path(__file__).with_name("Feedthrough.fmu")
target.write_bytes(fmu)
print(f"Prepared {target}")
