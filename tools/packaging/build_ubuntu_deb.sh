#!/bin/sh
# Build from dist/Paper-Radar created on Linux by packaging/build_desktop.py.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
VERSION=$(cd "$ROOT" && python -c 'from paper_radar import __version__;print(__version__)')
ARCH=$(dpkg --print-architecture)
STAGE="$ROOT/build/deb-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/opt/paper-radar" "$STAGE/DEBIAN" "$STAGE/usr/share/applications" "$STAGE/usr/bin"
cp -R "$ROOT/dist/Paper-Radar/." "$STAGE/opt/paper-radar/"
chmod 755 "$STAGE/opt/paper-radar/Paper-Radar"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: paper-radar
Version: $VERSION
Section: science
Priority: optional
Architecture: $ARCH
Maintainer: Paper-Radar contributors
Description: Local research paper discovery and evidence workspace
 Python and desktop dependencies are bundled. User data is stored in
 their own home directory, separate from read-only application files.
EOF
cat > "$STAGE/usr/share/applications/paper-radar.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Paper-Radar
Comment=Literature research workspace and runtime monitor
Exec=/opt/paper-radar/Paper-Radar
Terminal=false
Categories=Education;Science;
StartupNotify=true
EOF
cat > "$STAGE/usr/bin/paper-radar" <<'SHBIN'
#!/bin/sh
exec /opt/paper-radar/Paper-Radar "$@"
SHBIN
chmod 755 "$STAGE/usr/bin/paper-radar"
dpkg-deb --build --root-owner-group "$STAGE" "$ROOT/dist/Paper-Radar_${VERSION}_${ARCH}.deb"
