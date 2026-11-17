#!/usr/bin/env python3
"""Build the no-install Windows bundle with an embedded Python runtime.

The bundle is intentionally not committed. Run from the project root:

    python tools/build_windows_bundle.py

Output:
    dist/Global-Research-Timeline-<version>-win64.zip
    dist/SHA256SUMS.txt
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
PYTHON_VERSION = "3.12.10"
PYTHON_TAG = "python312"

RUN_BAT = r"""@echo off
setlocal
cd /d "%~dp0"
if not defined OPEN_BROWSER set "OPEN_BROWSER=1"
"%~dp0python\python.exe" "%~dp0server\app.py"
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [ERROR] Server exited with code %CODE%.
  echo Copy the message above when asking for help.
  echo.
  pause
)
endlocal
"""

README_TXT = """Global Research Timeline v{version}
Windows 免安装版

1. 双击 run.bat
2. 浏览器会自动打开 http://127.0.0.1:8770
3. 关闭黑色窗口即停止服务

数据保存在 data\\research.db。备份时复制这个文件即可。

如果浏览器没有自动打开，请手动访问：
http://127.0.0.1:8770

如果 Windows 提示“已保护你的电脑”：
右键 run.bat -> 属性 -> 勾选“解除锁定” -> 确定，然后再运行。
也可以先把 zip 文件解除锁定，再解压。

如果 8770 端口被占用：
服务会自动尝试 8771-8789，启动窗口会打印实际地址。

不要删除 python 文件夹；它是本包自带的 Python 运行时，
不需要另外安装 Python。

详细说明见 README.md。
"""


def version() -> str:
    text = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("cannot find VERSION in server/app.py")
    return match.group(1)


def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 1_000_000:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    urllib.request.urlretrieve(url, target)


def copy_project(stage: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for name in ("server", "web", "config"):
        shutil.copytree(ROOT / name, stage / name, ignore=ignore)
    for name in ("LICENSE", "README.md"):
        shutil.copy2(ROOT / name, stage / name)


def build() -> tuple[Path, Path]:
    ver = version()
    name = f"Global-Research-Timeline-{ver}-win64"
    stage = BUILD / "stage" / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    archive = BUILD / "cache" / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    download(
        f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
        archive,
    )
    py_dir = stage / "python"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(py_dir)

    # The embeddable runtime ignores PYTHONPATH, so add the server directory
    # to its path file; otherwise app.py cannot import its sibling modules.
    pth = py_dir / f"{PYTHON_TAG}._pth"
    pth.write_text(f"{PYTHON_TAG}.zip\n.\n..\\server\n", encoding="utf-8")

    copy_project(stage)
    (stage / "run.bat").write_bytes(
        RUN_BAT.replace("\n", "\r\n").encode("utf-8")
    )
    (stage / "README.txt").write_text(
        README_TXT.format(version=ver), encoding="utf-8-sig", newline="\r\n"
    )

    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(stage.parent))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sums = DIST / "SHA256SUMS.txt"
    sums.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    print(f"built {zip_path} ({zip_path.stat().st_size:,} bytes)")
    print(f"sha256 {digest}")
    return zip_path, sums


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
