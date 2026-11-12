# Hartmann Studios F-Droid repository

Our own F-Droid repo for the Hartmann Studios Android apps (the Stripe "direct" builds).
The main F-Droid.org store doesn't take apps with proprietary ads or billing, so we host our own.
Not affiliated with F-Droid.org.

**Repo address:** `https://charleshartmann.com/fdroid/repo`
**Fingerprint (SHA-256):** `20E8FD0361AB592680554092E282BC865135F64D388C72C4E7D12DA8D3260FC4`

Add it once in F-Droid (Settings → Repositories → +), Neo Store or Droid-ify, or open this on the phone:
https://charleshartmann.com/fdroid/repo?fingerprint=20E8FD0361AB592680554092E282BC865135F64D388C72C4E7D12DA8D3260FC4

The apps then update automatically. Every app is marked honestly with its anti-features
(Ads, Tracking, NonFreeNet, NonFreeDep where they apply).

## How it works

1. `.github/workflows/build.yml` runs every hour (and on `workflow_dispatch` / a `repository_dispatch` of type `direct-release`).
2. `scripts/build.py plan` asks the GitHub API for each app in `apps.json`: the newest non-draft, non-prerelease release whose tag matches `tag_regex`, and its APK asset matching `asset_regex`. If nothing changed since the last bundle, the run stops there (about a minute).
3. When something changed, `build.py build` downloads every APK, reads each manifest (AdMob present → Ads/Tracking/NonFreeDep), writes `fdroid/metadata/*.yml` and icons, and runs `fdroid update` with the repo signing key (secrets `FDROID_KEYSTORE_B64`, `FDROID_KEY_PASS`).
4. The signed index and icons (no APKs) are uploaded as `repo-bundle.tar.gz` to the `repo-latest` release, with `apk-map.json` (APK file → GitHub release URL), `state.json` and `bundle.sha256`.
5. On the charleshartmann.com server, `/usr/local/sbin/hs-fdroid-pull` (cron every 10 min, `server/hs-fdroid-pull.sh`) checks `bundle.sha256`, verifies and swaps in the new index under `/var/www/fdroid/repo`, and rewrites the nginx map so every `repo/*.apk` 302-redirects to the GitHub release asset. APKs are never stored on the web server.

## Common jobs

- **New release of an existing app:** nothing to do. Publish the GitHub release (not as a prerelease); the next hourly run picks it up and the server serves it within ~10 minutes. To skip the wait: Actions → Build F-Droid repo → Run workflow.
- **Add an app:** add an entry to `apps.json` (repo, tag_regex, asset_regex, title, summary, description, categories, icon_url, type) and push.
- **Remove an app:** delete its entry from `apps.json`.
- **Force a rebuild:** run the workflow with `force` ticked, or bump `BUNDLE_VERSION` in `scripts/build.py`.
- **Server force-refresh:** `FORCE=1 /usr/local/sbin/hs-fdroid-pull`; log at `/var/log/hs-fdroid-pull.log`.

## Verify with a real client

Actions → "Verify with a real client (emulator)" → Run workflow. It boots an Android 13 emulator, installs Droid-ify,
adds this repo through the standard add-repo link, syncs, installs one app from it (default Flashlight) and uploads
screenshots + `result.json` as the `client-verify` artifact.

## Server files (charleshartmann.com, Ubuntu, nginx behind the Cloudflare tunnel)

- `/etc/nginx/snippets/hs-fdroid.conf` (from `server/hs-fdroid.conf`), included in both `conf.d/wordpress_http(s).conf` server blocks
- `/etc/nginx/conf.d/00-0-hs-fdroid-hash.conf` (`map_hash_bucket_size 256`, must load before the first map) and `conf.d/01-hs-fdroid-map.conf`
- `/etc/nginx/hs-fdroid-apk-map.conf` (written by the pull script), `/var/www/fdroid/` (index.html, qr.png, repo/)
- `/usr/local/sbin/hs-fdroid-pull` + `/etc/cron.d/hs-fdroid`
- Backups of the edited nginx files: `/root/tab-backup-2026-11-12-fdroid/`

Rollback: restore the two wordpress_http(s).conf files from that backup, delete the snippet, the two conf.d files and the cron file, `nginx -t && systemctl reload nginx`.

## Signing key

The repo signing key (PKCS12, alias `hartmannstudios-fdroid`) is kept in Tab's vault, a root-only copy on the server
(`/root/hs-fdroid-signing/`), and the two GitHub Actions secrets. Never commit it. Losing it means every user has to
re-add the repo, so keep the copies.
