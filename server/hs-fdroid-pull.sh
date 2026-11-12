#!/bin/bash
# hs-fdroid-pull: refresh https://charleshartmann.com/fdroid/repo from the
# chartmann1590/hartmann-studios-fdroid "repo-latest" release bundle.
# Installed at /usr/local/sbin/hs-fdroid-pull, run by /etc/cron.d/hs-fdroid every 10 min.
# Tab, Nov 12 2026 (task c3e94750). Log: /var/log/hs-fdroid-pull.log
set -euo pipefail
B=https://github.com/chartmann1590/hartmann-studios-fdroid/releases/download/repo-latest
W=/var/www/fdroid
L=/var/log/hs-fdroid-pull.log
M=/etc/nginx/hs-fdroid-apk-map.conf
exec 9>/run/hs-fdroid-pull.lock; flock -n 9 || exit 0
tmp=$(mktemp -d /tmp/hsfd.XXXXXX); trap 'rm -rf "$tmp"' EXIT
log(){ echo "$(date -u +%FT%TZ) $*" >> "$L"; }
curl -fsSL --max-time 60 "$B/bundle.sha256" -o "$tmp/bundle.sha256" || { log "bundle.sha256 fetch failed"; exit 0; }
if [ -z "${FORCE:-}" ] && cmp -s "$tmp/bundle.sha256" "$W/.bundle.sha256"; then exit 0; fi
curl -fsSL --max-time 900 "$B/repo-bundle.tar.gz" -o "$tmp/repo-bundle.tar.gz" || { log "bundle fetch failed"; exit 1; }
curl -fsSL --max-time 60 "$B/apk-map.json" -o "$tmp/apk-map.json" || { log "apk-map fetch failed"; exit 1; }
(cd "$tmp" && sha256sum -c --quiet bundle.sha256) || { log "checksum mismatch, kept the old repo"; exit 1; }
mkdir "$tmp/x"; tar -xzf "$tmp/repo-bundle.tar.gz" -C "$tmp/x"
if [ ! -s "$tmp/x/repo/index-v1.jar" ] || [ ! -s "$tmp/x/repo/entry.jar" ]; then log "bundle has no signed index, kept the old repo"; exit 1; fi
python3 - "$tmp/apk-map.json" > "$tmp/map.conf" <<'EOF'
import json, re, sys
m = json.load(open(sys.argv[1]))
for f, u in sorted(m.items()):
    assert re.fullmatch(r'[A-Za-z0-9._-]+\.apk', f), f
    assert re.fullmatch(r'https://github\.com/[A-Za-z0-9._/%-]+', u), u
    print(f'"/fdroid/repo/{f}" "{u}";')
EOF
mkdir -p "$W"
rm -rf "$W/repo.new" "$W/repo.old"
mv "$tmp/x/repo" "$W/repo.new"; chmod -R a+rX "$W/repo.new"
[ -d "$W/repo" ] && mv "$W/repo" "$W/repo.old"
mv "$W/repo.new" "$W/repo"; rm -rf "$W/repo.old"
if ! cmp -s "$tmp/map.conf" "$M"; then
  [ -f "$M" ] && cp -a "$M" "$M.prev"
  cp "$tmp/map.conf" "$M"
  if nginx -t >/dev/null 2>&1; then systemctl reload nginx; else [ -f "$M.prev" ] && cp "$M.prev" "$M"; log "nginx -t failed with the new map; map rolled back"; exit 1; fi
fi
cp "$tmp/bundle.sha256" "$W/.bundle.sha256"
log "updated: $(wc -l < "$M") apks in the redirect map"
