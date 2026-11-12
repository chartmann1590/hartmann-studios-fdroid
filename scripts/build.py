#!/usr/bin/env python3
"""Hartmann Studios F-Droid repo builder (runs in GitHub Actions).

  python scripts/build.py plan   -> decide whether anything changed (writes plan.json, sets output changed=true/false)
  python scripts/build.py build  -> download the APKs, write metadata, run `fdroid update`, write dist/

The APKs themselves are NOT published here: the web server 302-redirects every
repo/<file>.apk request to the GitHub release asset (dist/apk-map.json).
"""
import hashlib, json, os, re, shutil, subprocess, sys, tarfile, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FD = os.path.join(ROOT, 'fdroid')
DIST = os.path.join(ROOT, 'dist')
SELF = os.environ.get('GITHUB_REPOSITORY', 'chartmann1590/hartmann-studios-fdroid')
REPO_URL = os.environ.get('FDROID_REPO_URL', 'https://charleshartmann.com/fdroid/repo')
TOKEN = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
BUNDLE_VERSION = 3   # bump to force a rebuild after changing this script's output


def api(path):
    req = urllib.request.Request('https://api.github.com' + path, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'hs-fdroid'})
    if TOKEN:
        req.add_header('Authorization', 'Bearer ' + TOKEN)
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == 3:
                raise
        except Exception:
            if i == 3:
                raise
        time.sleep(3 * (i + 1))


def output(k, v):
    p = os.environ.get('GITHUB_OUTPUT')
    if p:
        with open(p, 'a') as f:
            f.write(f'{k}={v}\n')
    print(f'{k}={v}')


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def pick(app):
    rels = api(f"/repos/{app['repo']}/releases?per_page=50") or []
    tre, are = re.compile(app['tag_regex']), re.compile(app['asset_regex'])
    for r in rels:  # newest first
        if r.get('draft') or r.get('prerelease') or not tre.search(r.get('tag_name', '')):
            continue
        assets = [a for a in r.get('assets', []) if are.search(a['name']) and not re.search(r'(?i)play', a['name'])]
        if assets:
            a = assets[0]
            safe = re.sub(r'[^A-Za-z0-9._-]', '_', r['tag_name'])
            return {'key': app['key'], 'tag': r['tag_name'], 'asset': a['name'], 'url': a['browser_download_url'],
                    'size': a['size'], 'updated_at': a['updated_at'], 'file': f"{app['key']}_{safe}.apk",
                    'release_url': r['html_url']}
    return {'key': app['key'], 'error': 'no matching release'}


def plan():
    apps = json.load(open(os.path.join(ROOT, 'apps.json')))['apps']
    with ThreadPoolExecutor(8) as ex:
        picks = list(ex.map(pick, apps))
    h = hashlib.sha256()
    for p in (os.path.join(ROOT, 'apps.json'), os.path.abspath(__file__)):
        h.update(open(p, 'rb').read())
    h.update(json.dumps([[p.get('key'), p.get('url'), p.get('size'), p.get('updated_at')] for p in picks]).encode())
    h.update(str(BUNDLE_VERSION).encode())
    want = h.hexdigest()
    prev = None
    try:
        with urllib.request.urlopen(f'https://github.com/{SELF}/releases/download/repo-latest/state.json', timeout=30) as r:
            prev = json.load(r).get('input_hash')
    except Exception as e:
        print('no previous state:', e)
    json.dump({'input_hash': want, 'picks': picks}, open(os.path.join(ROOT, 'plan.json'), 'w'), indent=1)
    errs = [p for p in picks if 'error' in p]
    print(f'{len(picks) - len(errs)} apps resolved, {len(errs)} without a release: {[p["key"] for p in errs]}')
    force = os.environ.get('FORCE', '').lower() == 'true'
    output('changed', 'true' if (force or want != prev) else 'false')


def manifest_flags(apk_path):
    """Read the APK manifest: is Google AdMob in it? (drives the Ads/Tracking anti-features)."""
    try:
        try:
            from loguru import logger
            logger.remove()
        except Exception:
            pass
        try:
            from androguard.core.apk import APK
        except ImportError:
            from androguard.core.bytecodes.apk import APK
        a = APK(apk_path)
        xml = a.get_android_manifest_xml()
        ns = '{http://schemas.android.com/apk/res/android}name'
        metas = [m.get(ns) for m in xml.iter('meta-data')]
        acts = [m.get(ns) for m in xml.iter('activity')]
        perms = a.get_permissions()
        admob = 'com.google.android.gms.ads.APPLICATION_ID' in metas or any((x or '').startswith('com.google.android.gms.ads') for x in acts)
        return {'admob': admob, 'internet': 'android.permission.INTERNET' in perms, 'package': a.get_package()}
    except Exception as e:
        print('manifest read failed', apk_path, e)
        return {'admob': True, 'internet': True, 'package': None}  # assume the worst: mark ads


def yq(s):
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid YAML scalars


def download(p):
    dst = os.path.join(FD, 'repo', p['file'])
    if os.path.exists(dst) and os.path.getsize(dst) == p['size']:
        return p
    for i in range(4):
        try:
            tmp = dst + '.part'
            with urllib.request.urlopen(urllib.request.Request(p['url'], headers={'User-Agent': 'hs-fdroid'}), timeout=120) as r, open(tmp, 'wb') as f:
                shutil.copyfileobj(r, f, 1 << 20)
            if os.path.getsize(tmp) != p['size']:
                raise IOError('size mismatch')
            os.replace(tmp, dst)
            return p
        except Exception as e:
            print('retry', p['file'], e)
            time.sleep(5 * (i + 1))
    raise SystemExit('download failed: ' + p['file'])


def build():
    pl = json.load(open(os.path.join(ROOT, 'plan.json')))
    apps = {a['key']: a for a in json.load(open(os.path.join(ROOT, 'apps.json')))['apps']}
    picks = [p for p in pl['picks'] if 'error' not in p]
    os.makedirs(os.path.join(FD, 'repo', 'icons'), exist_ok=True)
    os.makedirs(os.path.join(FD, 'metadata'), exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(download, picks))
    print('downloaded', len(picks))
    shutil.copy(os.path.join(ROOT, 'assets', 'repo-icon.png'), os.path.join(FD, 'repo', 'icons', 'repo-icon.png'))
    repoinfo = {}
    for p in picks:
        app = apps[p['key']]
        apk = os.path.join(FD, 'repo', p['file'])
        fl = manifest_flags(apk)
        pkg = fl['package'] or app['package_hint']
        p['package'] = pkg
        p['sha256'] = sha(apk)
        r = repoinfo.get(app['repo'])
        if r is None:
            r = repoinfo[app['repo']] = api('/repos/' + app['repo']) or {}
        lic = ((r.get('license') or {}).get('spdx_id') or '')
        if lic in ('', 'NOASSERTION'):
            lic = 'LicenseRef-Proprietary'
        af = {}
        if fl['admob']:
            af['Ads'] = 'Shows Google AdMob ads on the free tier. Pro / ad-free removes them.'
            af['Tracking'] = 'Google AdMob collects device and usage data for ad targeting and measurement.'
            af['NonFreeDep'] = 'Uses the proprietary Google Mobile Ads (AdMob) SDK.'
        nf = []
        if fl['admob']:
            nf.append('Google AdMob')
        if app.get('type') in ('billing', 'game'):
            nf.append('Stripe Checkout and the Hartmann Studios billing server for the paid Pro upgrade')
        if nf:
            af['NonFreeNet'] = 'Talks to non-free network services: ' + ', '.join(nf) + '.'
        p['antifeatures'] = sorted(af)
        desc = app['description'].strip()
        extra = []
        if app.get('type') in ('billing', 'game'):
            extra.append('Free to download. An optional paid upgrade (Pro / ad-free) is bought inside the app through secure Stripe checkout and can be restored with your email.')
        else:
            extra.append('Free to download.')
        if fl['admob']:
            extra.append('The free version shows ads.')
        extra.append('This is the official "direct" build from Hartmann Studios, the same app as on our website and GitHub releases. Updates arrive through this repository.')
        lines = [
            'Name: ' + yq(app['title']),
            'Summary: ' + yq(app['summary'][:80]),
            'Description: ' + yq(desc + '\n\n' + ' '.join(extra)),
            'AuthorName: Hartmann Studios',
            'AuthorEmail: hartmannstudios@mail.tab.bot',
            'WebSite: https://charleshartmann.com/apps/',
            'License: ' + lic,
            'Categories:',
        ] + ['  - ' + yq(c) for c in app['categories']]
        if not r.get('private') and r.get('html_url'):
            lines += ['SourceCode: ' + r['html_url'], 'IssueTracker: ' + r['html_url'] + '/issues']
        lines.append('Changelog: ' + p['release_url'])
        if af:
            lines.append('AntiFeatures:')
            for k in sorted(af):
                lines += [f'  {k}:', '    en-US: ' + yq(af[k])]
        open(os.path.join(FD, 'metadata', pkg + '.yml'), 'w').write('\n'.join(lines) + '\n')
        if app.get('icon_url'):
            try:
                from PIL import Image
                import io
                with urllib.request.urlopen(urllib.request.Request(app['icon_url'], headers={'User-Agent': 'Mozilla/5.0 hs-fdroid'}), timeout=30) as resp:
                    im = Image.open(io.BytesIO(resp.read())).convert('RGBA')
                d = os.path.join(FD, 'metadata', pkg, 'en-US')
                os.makedirs(d, exist_ok=True)
                im.resize((512, 512)).save(os.path.join(d, 'icon.png'))
            except Exception as e:
                print('icon failed', p['key'], e)
    ks = os.path.join(FD, 'keystore.p12')
    if os.environ.get('FDROID_KEYSTORE_B64'):
        import base64
        open(ks, 'wb').write(base64.b64decode(os.environ['FDROID_KEYSTORE_B64']))
        os.chmod(ks, 0o600)
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or '/usr/local/lib/android/sdk'
    cfg = f"""repo_url: {REPO_URL}
repo_name: Hartmann Studios
repo_icon: repo-icon.png
repo_description: >-
  Official apps from Hartmann Studios (Charles Hartmann). These are the "direct"
  builds: free to download, some show ads, and an optional Pro upgrade is bought
  in the app through Stripe. Not affiliated with F-Droid.org.
archive_older: 0
sdk_path: {sdk}
keystore: keystore.p12
repo_keyalias: hartmannstudios-fdroid
keystorepass: {{env: FDROID_KEY_PASS}}
keypass: {{env: FDROID_KEY_PASS}}
keydname: CN=Hartmann Studios F-Droid Repo, O=Hartmann Studios, C=US
"""
    open(os.path.join(FD, 'config.yml'), 'w').write(cfg)
    os.chmod(os.path.join(FD, 'config.yml'), 0o600)
    subprocess.run(['fdroid', 'update', '--pretty'], cwd=FD, check=True)
    repo = os.path.join(FD, 'repo')
    idx = json.load(open(os.path.join(repo, 'index-v1.json')))
    for f in ('index-v1.jar', 'entry.jar', 'index-v2.json'):
        if not os.path.exists(os.path.join(repo, f)):
            raise SystemExit('missing ' + f)
    files_in_index = {v['apkName'] for vs in idx['packages'].values() for v in vs}
    apkmap = {p['file']: p['url'] for p in picks if p['file'] in files_in_index}
    missing = [p['key'] for p in picks if p['file'] not in files_in_index]
    tar = os.path.join(DIST, 'repo-bundle.tar.gz')
    with tarfile.open(tar, 'w:gz') as t:
        for dp, dn, fn in os.walk(repo):
            for f in fn:
                if f.endswith('.apk') or '.apk.' in f or f.endswith('.part'):
                    continue
                full = os.path.join(dp, f)
                t.add(full, arcname=os.path.join('repo', os.path.relpath(full, repo)))
    json.dump(apkmap, open(os.path.join(DIST, 'apk-map.json'), 'w'), indent=1)
    state = {'input_hash': pl['input_hash'], 'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'apps_in_index': len(idx['packages']), 'not_in_index': missing,
             'apps': [{k: p.get(k) for k in ('key', 'package', 'tag', 'file', 'url', 'sha256', 'antifeatures')} for p in picks],
             'bundle_sha256': sha(tar), 'apk_map_sha256': sha(os.path.join(DIST, 'apk-map.json'))}
    json.dump(state, open(os.path.join(DIST, 'state.json'), 'w'), indent=1)
    open(os.path.join(DIST, 'bundle.sha256'), 'w').write(state['bundle_sha256'] + '  repo-bundle.tar.gz\n' + state['apk_map_sha256'] + '  apk-map.json\n')
    os.remove(ks) if os.path.exists(ks) else None
    print(f"index has {len(idx['packages'])} apps; not in index: {missing}")


if __name__ == '__main__':
    {'plan': plan, 'build': build}[sys.argv[1]]()
