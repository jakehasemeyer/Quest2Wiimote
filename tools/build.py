"""Build/sign the native APK without Android Studio, Gradle, or global installs."""
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'work')


def run(*args):
    print('>', Path(str(args[0])).name, ' '.join(map(str, args[1:])), flush=True)
    subprocess.run(list(map(str, args)), check=True, cwd=ROOT)


def main():
    tools = ROOT / 'tools'
    ndk = next((tools/'ndk').glob('android-ndk-*'))
    llvm = ndk/'toolchains/llvm/prebuilt/windows-x86_64/bin'
    build = ROOT/'work/apk'
    build.mkdir(parents=True, exist_ok=True)
    run(tools/'desktop-venv/Scripts/python.exe',ROOT/'tools/quest_models.py',build/'models')
    bt = next(p for p in (tools/'android-build-tools').iterdir() if p.is_dir())
    platform = next((tools/'android-platform').glob('*/android.jar'))
    jdk = next((tools/'jdk').glob('jdk-*'))
    os.environ['JAVA_HOME'] = str(jdk)
    os.environ['PATH'] = str(jdk/'bin') + os.pathsep + os.environ['PATH']
    glue = ndk/'sources/android/native_app_glue'
    common = ['--target=aarch64-linux-android29', '-O2', '-fPIC', '-I'+str(glue)]
    run(llvm/'clang.exe', *common, '-c', glue/'android_native_app_glue.c', '-o', build/'glue.o')
    run(llvm/'clang++.exe', *common, '-std=c++17', '-shared', '-static-libstdc++', '-I'+str(tools/'include'),
        ROOT/'quest/main.cpp', build/'glue.o', '-L'+str(tools/'openxr-loader/jni/arm64-v8a'),
        '-lopenxr_loader', '-landroid', '-llog', '-lEGL', '-lGLESv3', '-Wl,-z,max-page-size=16384',
        '-Wl,-soname,libquest2wiimote.so', '-o', build/'libquest2wiimote.so')
    unsigned = build/'unsigned.apk'
    run(bt/'aapt2.exe', 'link', '--manifest', ROOT/'quest/AndroidManifest.xml', '-I', platform, '-o', unsigned)
    with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_STORED) as z:
        z.write(build/'libquest2wiimote.so', 'lib/arm64-v8a/libquest2wiimote.so')
        z.write(tools/'openxr-loader/jni/arm64-v8a/libopenxr_loader.so', 'lib/arm64-v8a/libopenxr_loader.so')
        for model in (build/'models').glob('*.q2cm'):
            z.write(model,'assets/'+model.name,compress_type=zipfile.ZIP_DEFLATED)
    aligned = build/'aligned.apk'
    run(bt/'zipalign.exe', '-P', '16', '-f', '4', unsigned, aligned)
    key = tools/'debug.keystore'
    if not key.exists():
        run(jdk/'bin/keytool.exe', '-genkeypair', '-keystore', key, '-storepass', 'android', '-keypass', 'android',
            '-alias', 'androiddebugkey', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000', '-dname', 'CN=Quest2Wiimote Local Development')
    apk = ROOT/'dist/Quest2Wiimote.apk'
    run(jdk/'bin/java.exe', '-Djava.io.tmpdir='+str(ROOT/'work'), '-jar', bt/'lib/apksigner.jar', 'sign',
        '--ks', key, '--ks-pass', 'pass:android', '--out', apk, aligned)
    run(jdk/'bin/java.exe', '-jar', bt/'lib/apksigner.jar', 'verify', '--verbose', apk)
    print('Built:', apk, flush=True)


if __name__ == '__main__':
    main()
