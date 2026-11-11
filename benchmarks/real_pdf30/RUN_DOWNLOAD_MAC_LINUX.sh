#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 scripts/download_official_pdfs.py --manifest manifest.json --dest pdfs
