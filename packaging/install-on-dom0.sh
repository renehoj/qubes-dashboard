#!/bin/bash
# Install or update qubes-dashboard on dom0, end to end.
#
# Same route as qubes-launcher's packaging/update-on-dom0.sh: dom0 has no
# networking, so a staging qube (over `qvm-run -p`) clones the repo fresh
# from the git server -- public read, no credentials -- stamps the checkout
# with the commit it came from, and streams one tarball back. dom0 validates
# the archive and only then installs:
#
#   ~/.local/share/qubes-dashboard   the list itself (run via its bin/), with
#                                    the Settings, Firewall, Devices,
#                                    Information, Logs and Backup windows
#                                    under tools/; bin/qubes-dashboard puts
#                                    those on PYTHONPATH
#   ~/.config/systemd/user/qubes-dashboard.service (+ a drop-in pointing
#                                     ExecStart at the checkout above)
#   ~/.local/share/dbus-1/services/dbus-qubes-dashboard.service
#   ~/.config/autostart/qubes-dashboard.desktop
#   ~/.local/share/applications/open-qubes-dashboard.desktop
#
# then (re)starts the resident instance and proves it came up. Running it
# again is an update. Trees an earlier version installed next to it
# (~/.local/share/qubes-settings and the like, from when the windows were
# repos of their own) are removed.
#
# Consequences worth knowing:
#   * the install tracks origin/main -- committed but not PUSHED work does
#     not deploy;
#   * the staging qube needs `git` and must resolve the git server;
#   * BUILD-COMMIT in the installed tree says exactly which commit it is.
#
# Trust note: whatever the staging qube hands over becomes code running in
# dom0. If that ever stops feeling like enough, the place to verify a
# signature is step 2, before anything is extracted.
#
# Usage: install-on-dom0.sh [staging-qube]      (default: personal)
#
# Environment override:
#   QUBES_DASHBOARD_REPO  -- clone URL of qubes-dashboard

set -euo pipefail

DASHBOARD_URL="${QUBES_DASHBOARD_REPO:-https://git.homelab.computer/homelab/qubes-dashboard.git}"
STAGING_VM="${1:-personal}"      # the qube that talks to the git server
TARBALL="qubes-dashboard.tgz"   # local name for the streamed archive
TOP_DIR="qubes-dashboard-build" # top-level directory inside the tarball
SHARE_DIR="$HOME/.local/share"
SERVICE="qubes-dashboard.service"
UNIT_DIR="$HOME/.config/systemd/user"
TOOLS="qubes-settings qubes-firewall qubes-devices qubes-information qubes-logs qubes-backup"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# 1. Build on the staging qube and stream the tarball back. Everything
#    between the heredoc markers runs THERE, not here. Its stdout is the
#    tarball and nothing else may write to it; set -e makes a failed clone
#    abort before anything installed is touched.
qvm-run -p "$STAGING_VM" \
    "DASHBOARD_URL='$DASHBOARD_URL' bash -s" \
    > "$workdir/$TARBALL" <<'EOS'
set -eu
src="$(mktemp -d)"
trap 'rm -rf "$src"' EXIT
mkdir "$src/qubes-dashboard-build"
cd "$src/qubes-dashboard-build"
git clone --quiet --depth 1 "$DASHBOARD_URL" qubes-dashboard >&2
git -C qubes-dashboard rev-parse HEAD > qubes-dashboard/BUILD-COMMIT
rm -rf qubes-dashboard/.git
tar czf - -C "$src" qubes-dashboard-build
EOS

# 2. Prove the archive is whole and carries its provenance BEFORE anything
#    installed is touched.
tar tf "$workdir/$TARBALL" >/dev/null
tar xf "$workdir/$TARBALL" -C "$workdir"
build="$workdir/$TOP_DIR"
checks="qubes-dashboard/bin/qubes-dashboard qubes-dashboard/packaging/qubes-dashboard.service"
for tool in $TOOLS; do
    checks="$checks qubes-dashboard/tools/$tool/qubes_${tool#qubes-}/window.py"
done
for check in $checks; do
    if [ ! -f "$build/$check" ]; then
        echo "install: $check missing from the archive" >&2
        exit 1
    fi
done
commit_file="$build/qubes-dashboard/BUILD-COMMIT"
if [ ! -f "$commit_file" ] || ! grep -Eq '^[0-9a-f]{40}$' "$commit_file"; then
    echo "install: qubes-dashboard carries no valid BUILD-COMMIT" >&2
    exit 1
fi

# 3. Swap. Service stopped first (it may not exist yet on a first install):
#    a hotkey press mid-copy must not dbus-activate a half-written tree. The
#    tree is copied next to its target and renamed into place.
systemctl --user stop "$SERVICE" 2>/dev/null || true
mkdir -p "$SHARE_DIR"
rm -rf "$SHARE_DIR/.qubes-dashboard.new"
cp -r "$build/qubes-dashboard" "$SHARE_DIR/.qubes-dashboard.new"
rm -rf "$SHARE_DIR/qubes-dashboard"
mv "$SHARE_DIR/.qubes-dashboard.new" "$SHARE_DIR/qubes-dashboard"
chmod 755 "$SHARE_DIR/qubes-dashboard/bin/qubes-dashboard"
# The separate trees an earlier version of this script installed; only ones
# it stamped with BUILD-COMMIT, so a tree put there by hand stays.
for tool in $TOOLS; do
    if [ -f "$SHARE_DIR/$tool/BUILD-COMMIT" ]; then
        rm -rf "$SHARE_DIR/$tool"
    fi
done

# 4. Unit, drop-in, D-Bus activation and autostart. The packaged unit's
#    ExecStart assumes `pip install .`; the drop-in runs the checkout instead.
pkg="$SHARE_DIR/qubes-dashboard/packaging"
install -Dm644 "$pkg/qubes-dashboard.service" "$UNIT_DIR/$SERVICE"
mkdir -p "$UNIT_DIR/$SERVICE.d"
cat > "$UNIT_DIR/$SERVICE.d/checkout.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=%h/.local/share/qubes-dashboard/bin/qubes-dashboard --background
EOF
install -Dm644 "$pkg/dbus-qubes-dashboard.service" \
    "$SHARE_DIR/dbus-1/services/dbus-qubes-dashboard.service"
install -Dm644 "$pkg/autostart/qubes-dashboard.desktop" \
    "$HOME/.config/autostart/qubes-dashboard.desktop"
install -Dm644 "$pkg/open-qubes-dashboard.desktop" \
    "$SHARE_DIR/applications/open-qubes-dashboard.desktop"

# 5. Start and prove it came up. The unit is Type=dbus, so the start job only
#    succeeds once the bus name is owned: a crash-on-start fails the job and
#    therefore this script.
systemctl --user daemon-reload
systemctl --user start "$SERVICE"
systemctl --user is-active --quiet "$SERVICE"

echo "qubes-dashboard $(cut -c1-10 "$SHARE_DIR/qubes-dashboard/BUILD-COMMIT") installed and active"
