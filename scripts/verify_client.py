#!/usr/bin/env python3
"""Drive a real F-Droid-compatible client (Droid-ify) on an Android emulator:
add the Hartmann Studios repo, sync, install one app. Writes evidence to out/."""
import os, re, subprocess, sys, time, json
REPO = os.environ['REPO_ADDR']; FP = os.environ['REPO_FP']; PKG = os.environ.get('TEST_PKG', 'com.charles.flashlight')
CLIENT = os.environ.get('CLIENT_PKG', 'com.looker.droidify')
os.makedirs('out', exist_ok=True)
log = open('out/steps.log', 'a')
def sh(c, check=False, t=120):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t)
    log.write(f'$ {c}\n{r.stdout[-2000:]}{r.stderr[-1000:]}\n'); log.flush()
    return r.stdout
n = [0]
def shot(name):
    n[0] += 1
    sh(f'adb exec-out screencap -p > out/{n[0]:02d}-{name}.png')
def dump():
    sh('adb shell uiautomator dump /sdcard/ui.xml >/dev/null; adb pull /sdcard/ui.xml out/ui.xml >/dev/null')
    try: return open('out/ui.xml', encoding='utf-8', errors='replace').read()
    except Exception: return ''
def find(words):
    x = dump()
    for m in re.finditer(r'<node [^>]*>', x):
        node = m.group(0)
        t = (re.search(r' text="([^"]*)"', node) or [None, ''])[1]
        d = (re.search(r' content-desc="([^"]*)"', node) or [None, ''])[1]
        for w in words:
            if w.lower() == t.strip().lower() or w.lower() == d.strip().lower():
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                if b:
                    x1, y1, x2, y2 = map(int, b.groups())
                    return w, ((x1 + x2) // 2, (y1 + y2) // 2)
    return None, None
def tap(words, tries=10, wait=3):
    for i in range(tries):
        w, xy = find(words)
        if xy:
            sh(f'adb shell input tap {xy[0]} {xy[1]}'); log.write(f'TAP {w} {xy}\n'); time.sleep(2); return w
        time.sleep(wait)
    return None
def texts():
    return re.findall(r' text="([^"]+)"', dump())
res = {'client': CLIENT, 'repo': REPO, 'test_pkg': PKG}
sh('adb install -r client.apk', t=300)
sh(f'adb shell appops set {CLIENT} REQUEST_INSTALL_PACKAGES allow')
sh(f'adb shell pm grant {CLIENT} android.permission.POST_NOTIFICATIONS')
sh(f'adb shell monkey -p {CLIENT} -c android.intent.category.LAUNCHER 1'); time.sleep(12); shot('client-start')
for _ in range(3):
    tap(['Allow', 'OK', 'Got it', 'Continue', 'Skip', 'Next', 'Accept'], tries=2, wait=2)
# add the repo through the standard add-repo deep link
sh(f'adb shell am start -a android.intent.action.VIEW -d "{REPO}?fingerprint={FP}" {CLIENT}'); time.sleep(6); shot('add-repo-screen')
res['add_tap'] = tap(['Add', 'Save', 'Add repository', 'OK', 'Enable'], tries=6)
time.sleep(5); shot('after-add')
res['texts_after_add'] = texts()[:40]
# give the sync time (our index + the client's default repos)
time.sleep(60); shot('after-sync')
# open the app page and install
for attempt in range(6):
    sh(f'adb shell am start -a android.intent.action.VIEW -d "market://details?id={PKG}" {CLIENT}'); time.sleep(8)
    shot(f'app-page-{attempt}')
    w = tap(['Install', 'INSTALL', 'Download'], tries=3)
    if w:
        res['install_tap'] = w; break
    time.sleep(30)
time.sleep(25); shot('installer')
res['confirm_tap'] = tap(['Install', 'INSTALL', 'Update', 'OK'], tries=8, wait=4)
installed = False
for i in range(30):
    if PKG in sh(f'adb shell pm list packages {PKG}'):
        installed = True; break
    if i % 5 == 4:
        tap(['Install', 'INSTALL'], tries=1); shot(f'wait-{i}')
    time.sleep(6)
res['installed'] = installed
if installed:
    res['version'] = sh(f'adb shell dumpsys package {PKG} | grep -m2 -E "versionName|versionCode"').strip()
    sh(f'adb shell monkey -p {PKG} -c android.intent.category.LAUNCHER 1'); time.sleep(10); shot('test-app-launched')
    res['test_app_top'] = sh('adb shell dumpsys activity activities | grep -m1 -E "topResumedActivity|mResumedActivity"').strip()
shot('final')
json.dump(res, open('out/result.json', 'w'), indent=1)
print(json.dumps(res, indent=1))
sys.exit(0 if installed else 1)
