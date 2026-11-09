"""Portable build tools. Downloads, extraction, caches and temporary files stay here."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
CACHE = TOOLS / 'downloads'
CACHE.mkdir(exist_ok=True)
(ROOT / 'work').mkdir(exist_ok=True)
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'work')


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Quest2Wiimote'}), timeout=90).read()


def install(name, url, checksum=None, algorithm='sha256', extract=True):
    target = TOOLS / name
    if (target / '.ready').exists():
        return
    archive = CACHE / url.split('/')[-1]
    if not archive.exists():
        print('Downloading', name, flush=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Quest2Wiimote'}), timeout=120) as src, archive.with_suffix('.partial').open('wb') as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
        archive.with_suffix('.partial').replace(archive)
    if checksum:
        with archive.open('rb') as f:
            actual = hashlib.file_digest(f, algorithm).hexdigest()
        if actual.lower() != checksum.strip().lower():
            raise RuntimeError(f'Checksum mismatch: {archive}')
    if extract:
        print('Extracting', name, flush=True)
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
        (target / '.ready').touch()


def main():
    repo = ET.fromstring(fetch('https://dl.google.com/android/repository/repository2-3.xml'))
    wanted = {'platforms;android-32': 'android-platform', 'build-tools;35.0.0': 'android-build-tools', 'ndk;27.3.13750724': 'ndk', 'platform-tools': 'adb'}
    jobs = []
    for package in repo.findall('remotePackage'):
        if package.attrib['path'] in wanted:
            for archive in package.findall('archives/archive'):
                if archive.findtext('host-os') in (None, 'windows'):
                    jobs.append((wanted[package.attrib['path']], 'https://dl.google.com/android/repository/' + archive.findtext('complete/url'), archive.findtext('complete/checksum'), 'sha1'))
    releases = json.loads(fetch('https://api.github.com/repos/adoptium/temurin17-binaries/releases?per_page=10'))
    asset = next(a for r in releases if not r['prerelease'] for a in r['assets'] if a['name'].startswith('OpenJDK17U-jdk_x64_windows_hotspot_') and a['name'].endswith('.zip'))
    sha = fetch(asset['browser_download_url'] + '.sha256.txt').decode().split()[0]
    jobs.append(('jdk', asset['browser_download_url'], sha))
    scrcpy = json.loads(fetch('https://api.github.com/repos/Genymobile/scrcpy/releases/latest'))
    asset = next(a for a in scrcpy['assets'] if 'win64' in a['name'] and a['name'].endswith('.zip'))
    jobs.append(('scrcpy', asset['browser_download_url'], (asset.get('digest') or '').removeprefix('sha256:') or None))
    version = '1.1.65'
    maven = f'https://repo.maven.apache.org/maven2/org/khronos/openxr/openxr_loader_for_android/{version}/openxr_loader_for_android-{version}.aar'
    jobs.append(('openxr-loader', maven, fetch(maven + '.sha256').decode().strip()))
    (TOOLS / 'downloads.lock.json').write_text(json.dumps(jobs, indent=2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda j: install(*j), jobs))
    headers = TOOLS / 'include' / 'openxr'
    headers.mkdir(parents=True, exist_ok=True)
    for name in ['openxr.h', 'openxr_platform.h', 'openxr_platform_defines.h']:
        (headers / name).write_bytes(fetch(f'https://raw.githubusercontent.com/KhronosGroup/OpenXR-SDK/release-{version}/include/openxr/{name}'))
    vendor = ROOT / 'desktop' / 'web' / 'vendor'
    vendor.mkdir(exist_ok=True)
    for name in ['three.module.js', 'three.core.js']:
        (vendor / name).write_bytes(fetch(f'https://cdn.jsdelivr.net/npm/three@0.180.0/build/{name}'))
    orbit = fetch('https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js').decode().replace("from 'three'", "from './three.module.js'")
    (vendor / 'OrbitControls.js').write_text(orbit, encoding='utf-8')
    (vendor / 'THREE-LICENSE.txt').write_bytes(fetch('https://cdn.jsdelivr.net/npm/three@0.180.0/LICENSE'))
    print('Portable tools ready on E:.', flush=True)


if __name__ == '__main__':
    main()
