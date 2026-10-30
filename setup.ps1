$ErrorActionPreference = "Stop"

$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    & py -3 -m venv .venv
} else {
    python -m venv .venv
}

if ($LASTEXITCODE -ne 0) {
    throw "Could not create .venv. Install Python 3.11 or newer and retry."
}

& .\.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required."
}

& .\.venv\Scripts\python.exe -m geofactory init
if ($LASTEXITCODE -ne 0) {
    throw "GeoFactory initialization failed."
}

& .\.venv\Scripts\python.exe -m geofactory doctor
