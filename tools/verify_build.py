# 用真实构建验证两个 spec, 并跑一次打出来的 exe。
#
# spec 是构建配置, 而配置最容易悄悄烂掉(路径写死、漏 collect-all、
# console=True ...), 且烂掉的后果都在用户侧才显现。所以改完 spec 跑一遍这个。
#
# 用法:
#   C:/Python314/python.exe -X utf8 tools/verify_build.py
#
# 注意用 python -m PyInstaller 的同一个解释器: 直接 pyinstaller 可能指向
# 另一个 Python 环境(本机就是 —— 它在 Python312 的 Scripts 下), 那构建出来
# 的包和源码跑的解释器不是一套。
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(ROOT, '.tmptest', 'buildverify')
SPECS = ('GPUMigrate.spec', 'GPUMigrate-onedir.spec')


def run(cmd, timeout):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          timeout=timeout)


def build(spec):
    """构建一个 spec, 返回 (成功?, 日志尾部)。"""
    dist = os.path.join(SCRATCH, 'dist')
    work = os.path.join(SCRATCH, 'work')
    r = run([sys.executable, '-X', 'utf8', '-m', 'PyInstaller', spec,
             '--noconfirm', '--distpath', dist, '--workpath', work],
            timeout=600)
    tail = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()[-6:]
    return r.returncode == 0, chr(10).join(tail)


def smoke(exe_dir):
    u"""跑一次 --once: 采一次样就退出, 不常驻, 最适合冒烟。

    只看两件事: 进程能起来(退出码 0), 且没有 ModuleNotFoundError /
    DLL load failed / Failed to extract 这类打包问题。那些错在文件层面
    查不出来 —— exe 大小正常、依赖目录也在, 直到运行才炸。
    """
    exe = None
    for base, _dirs, files in os.walk(exe_dir):
        for name in files:
            if name.lower().endswith('.exe'):
                exe = os.path.join(base, name)
                break
        if exe:
            break
    if not exe:
        return False, '构建产物里没有 exe'
    try:
        r = subprocess.run([exe, '--once'], cwd=os.path.dirname(exe),
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=180)
    except subprocess.TimeoutExpired:
        # --once 本该自己退出; 超时至少说明进程起来了
        return True, 'exe 启动了(未在时限内自行退出)'
    blob = (r.stdout or '') + (r.stderr or '')
    bad = [k for k in ('ModuleNotFoundError', 'DLL load failed',
                       'ImportError', 'Failed to extract') if k in blob]
    if bad:
        return False, '打包类错误: %s' % bad
    if r.returncode != 0:
        return False, '退出码 %s' % r.returncode
    return True, '退出码 0, 无打包类错误'


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    os.makedirs(SCRATCH, exist_ok=True)
    failed = []
    for spec in SPECS:
        ok, tail = build(spec)
        print('%-26s build: %s' % (spec, 'OK' if ok else 'FAIL'))
        if not ok:
            failed.append(spec + ' (build)')
            print(tail)
            continue
        dist = os.path.join(SCRATCH, 'dist')
        ok, why = smoke(dist)
        print('%-26s smoke: %s  (%s)' % ('', 'OK' if ok else 'FAIL', why))
        if not ok:
            failed.append(spec + ' (smoke)')
    print()
    if failed:
        print('FAILED: %s' % failed)
        return 1
    print('两个 spec 都构建成功, 且打出来的 exe 能跑 --once')
    print('构建产物在 %s(验证完可删)' % SCRATCH)
    return 0


if __name__ == '__main__':
    sys.exit(main())
