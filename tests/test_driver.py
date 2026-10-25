"""PawnIO 驱动检测/安装逻辑的测试。

真正装驱动会弹 UAC、动系统状态,不适合进单元测试;
这里测的是那些"判断"部分——它们决定了程序会不会在错误的时机
去动用户的机器。
"""

from __future__ import annotations

import hashlib
import sys

import pytest

from pcmonitor import driver

# vendor/pawnio/README.md 里记录的那一份
_INSTALLER_SHA256 = "a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0"


def test_bundled_installer_exists():
    path = driver.installer_path()
    assert path is not None, "vendor/pawnio/PawnIO_setup.exe 应该在仓库里"
    assert path.name == "PawnIO_setup.exe"
    assert path.is_file()


def test_bundled_installer_checksum_matches_readme():
    """安装器是二进制,改坏了不会报错,只能靠校验和发现。"""
    path = driver.installer_path()
    assert path is not None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == _INSTALLER_SHA256


def test_installer_name_does_not_look_like_the_uninstaller():
    """官方安装器用 wcsstr(argv[0], "uninstall.exe") 判断自己是安装器还是卸载器。

    名字(或存放路径)里出现 "uninstall.exe" 的话,它会认为自己是卸载器,
    于是把 -install 抵消掉,弹一句 "Please specify either -install or
    -uninstall" 就退出。
    """
    assert "uninstall.exe" not in driver.INSTALLER_NAME.lower()


def test_install_refuses_an_uninstaller_looking_path(monkeypatch, tmp_path):
    fake = tmp_path / "uninstall.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setattr(driver, "installer_path", lambda: fake)
    result = driver.install()
    assert not result.ok
    assert "uninstall.exe" in result.message


def test_install_reports_missing_installer(monkeypatch):
    monkeypatch.setattr(driver, "installer_path", lambda: None)
    result = driver.install()
    assert not result.ok
    assert "PawnIO_setup.exe" in result.message


def test_is_installed_returns_bool():
    assert isinstance(driver.is_installed(), bool)


def test_is_elevated_returns_bool():
    assert isinstance(driver.is_elevated(), bool)


def test_probe_access_returns_none_or_int():
    hr = driver.probe_access()
    assert hr is None or isinstance(hr, int)


def test_self_command_for_source_run(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _exe, params = driver.self_command(["--json", "--once"])
    assert params.startswith("-m pcmonitor")
    assert "--json" in params


def test_self_command_quotes_arguments_with_spaces(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    _exe, params = driver.self_command(["--out", "C:\\我的 文件.txt"])
    assert '"C:\\我的 文件.txt"' in params


def test_elevate_self_does_nothing_when_already_admin(monkeypatch):
    monkeypatch.setattr(driver, "is_elevated", lambda: True)
    assert driver.elevate_self() is False


@pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上有意义")
def test_registry_probe_helpers_do_not_raise():
    """注册表查询在任何机器上都应该安静地返回 False,而不是抛异常。"""
    assert driver._registry_key_exists(r"SOFTWARE\\NoSuchKey\\AtAll") is False
