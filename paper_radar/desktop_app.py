# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Native desktop entry point (source mode or PyInstaller bundle).

By default opens a Tk controller with actual server/log monitoring. --serve
runs the same bundled executable as a localhost backend, so users do not need
Python, a terminal, virtual environments or a second executable.
"""
from __future__ import annotations
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if '--serve' in args:
        from paper_radar.webapp import run_server
        index=args.index('--port') if '--port' in args else -1
        try:port=int(args[index+1]) if index>=0 else 8765
        except (ValueError, IndexError):raise SystemExit('Invalid port')
        if not 1024<=port<=65535:raise SystemExit('Invalid port')
        run_server(port)
        return 0
    if '--once' in args:
        from main import run_once
        run_once()
        return 0
    if '--source-child' in args:
        from paper_radar.sources.source_process import main as source_main
        return source_main()
    if '--portal-worker' in args:
        from paper_radar.portal_worker import main as portal_worker_main
        return portal_worker_main(args[args.index('--portal-worker')+1:])
    if '--pdf-child' in args:
        from paper_radar.portal_pdf_worker import parse
        from pathlib import Path
        import json
        try:
            result=parse(Path(args[args.index('--pdf-child')+1]))
        except (OSError,RuntimeError,ValueError) as exc:
            print(json.dumps({'error':type(exc).__name__}))
            return 2
        print(json.dumps(result,ensure_ascii=False))
        return 0
    if '--doctor' in args or '--doctor-output' in args:
        from paper_radar.desktop_manager import doctor
        from pathlib import Path
        import json
        payload=json.dumps(doctor(), ensure_ascii=False, indent=2)
        if '--doctor-output' in args:
            try: output=Path(args[args.index('--doctor-output')+1])
            except IndexError:raise SystemExit('--doctor-output requires a filename')
            output.write_text(payload+'\n',encoding='utf-8')
        else:
            print(payload)
        return 0
    if args:raise SystemExit('Unknown argument')
    from paper_radar.desktop_manager import main as gui_main
    return gui_main()


if __name__=='__main__':raise SystemExit(main())
