#!/usr/bin/env python3
"""SSH 客户端：执行远程命令、交互式登录、上传/下载文件。

依赖:
    pip install paramiko

示例:
    python ssh_client.py exec --host 192.168.1.10 --user root -- uname -a
    python ssh_client.py shell --host 192.168.1.10 --user root --key ~/.ssh/id_rsa
    python ssh_client.py upload --host 192.168.1.10 --user root local.txt /tmp/local.txt
    python ssh_client.py download --host 192.168.1.10 --user root /etc/os-release ./os-release
"""

from __future__ import annotations

import argparse
import getpass
import os
import select
import socket
import stat
import sys
import termios
import tty
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

try:
    import paramiko
except ImportError:
    print("缺少依赖 paramiko，请先执行: pip install paramiko", file=sys.stderr)
    sys.exit(1)


DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 15.0


class SSHError(RuntimeError):
    """SSH 操作失败。"""


class SSHSession:
    def __init__(
        self,
        host: str,
        user: str,
        port: int = DEFAULT_PORT,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        look_for_keys: bool = True,
        allow_agent: bool = True,
        known_hosts: Optional[str] = None,
        strict_host_key: bool = True,
    ) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.password = password
        self.key_filename = expand_path(key_filename) if key_filename else None
        self.timeout = timeout
        self.look_for_keys = look_for_keys
        self.allow_agent = allow_agent
        self.known_hosts = expand_path(known_hosts) if known_hosts else str(Path.home() / ".ssh" / "known_hosts")
        self.strict_host_key = strict_host_key
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> paramiko.SSHClient:
        if self._client is not None:
            return self._client

        client = paramiko.SSHClient()
        if self.strict_host_key:
            client.load_system_host_keys()
            if Path(self.known_hosts).exists():
                client.load_host_keys(self.known_hosts)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                key_filename=self.key_filename,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=self.look_for_keys,
                allow_agent=self.allow_agent,
            )
        except paramiko.AuthenticationException as exc:
            raise SSHError(f"认证失败: {exc}") from exc
        except (socket.timeout, paramiko.SSHException, OSError) as exc:
            raise SSHError(f"连接 {self.user}@{self.host}:{self.port} 失败: {exc}") from exc

        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)

        self._client = client
        return client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SSHSession":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def exec(
        self,
        command: str,
        timeout: Optional[float] = None,
        get_pty: bool = False,
        environment: Optional[dict[str, str]] = None,
    ) -> tuple[int, str, str]:
        """执行远程命令，返回 (exit_code, stdout, stderr)。"""
        client = self.connect()
        stdin, stdout, stderr = client.exec_command(
            command,
            timeout=timeout or self.timeout,
            get_pty=get_pty,
            environment=environment,
        )
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, out, err

    def interactive_shell(self) -> int:
        """打开交互式伪终端。"""
        client = self.connect()
        term = os.environ.get("TERM", "xterm")
        width, height = _terminal_size()
        channel = client.invoke_shell(term=term, width=width, height=height)
        channel.settimeout(0.0)
        return _posix_shell(channel)

    @contextmanager
    def sftp(self) -> Iterator[paramiko.SFTPClient]:
        client = self.connect()
        sftp = client.open_sftp()
        try:
            yield sftp
        finally:
            sftp.close()

    def upload(self, local_path: str, remote_path: str) -> None:
        local = Path(local_path).expanduser().resolve()
        if not local.exists():
            raise SSHError(f"本地路径不存在: {local}")

        with self.sftp() as sftp:
            if local.is_dir():
                _upload_dir(sftp, local, remote_path)
            else:
                _ensure_remote_parent(sftp, remote_path)
                sftp.put(str(local), remote_path)
                print(f"已上传 {local} -> {self.user}@{self.host}:{remote_path}")

    def download(self, remote_path: str, local_path: str) -> None:
        local = Path(local_path).expanduser()
        with self.sftp() as sftp:
            try:
                attrs = sftp.stat(remote_path)
            except OSError as exc:
                raise SSHError(f"远程路径不存在: {remote_path}") from exc

            if stat.S_ISDIR(attrs.st_mode):
                _download_dir(sftp, remote_path, local)
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                dest = local / Path(remote_path).name if local.is_dir() else local
                sftp.get(remote_path, str(dest))
                print(f"已下载 {self.user}@{self.host}:{remote_path} -> {dest}")


def expand_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return path
    return str(Path(path).expanduser())


def _terminal_size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 80, 24


def _posix_shell(channel: paramiko.Channel) -> int:
    stdin_is_tty = sys.stdin.isatty()
    oldtty = termios.tcgetattr(sys.stdin) if stdin_is_tty else None
    try:
        if stdin_is_tty:
            tty.setraw(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        while True:
            readable, _, _ = select.select([channel, sys.stdin], [], [])
            if channel in readable:
                try:
                    data = channel.recv(32768)
                except socket.timeout:
                    continue
                if not data:
                    break
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
            if sys.stdin in readable:
                data = os.read(sys.stdin.fileno(), 1024)
                if not data:
                    break
                channel.send(data)
            if channel.exit_status_ready() and not channel.recv_ready():
                break
    finally:
        if oldtty is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)
        channel.close()

    return channel.recv_exit_status() if channel.exit_status_ready() else 0


def _ensure_remote_parent(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    parent = str(Path(remote_path).parent)
    if parent in {"", ".", "/"}:
        return
    _mkdir_p(sftp, parent)


def _mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    if not remote_dir or remote_dir == "/":
        return
    parts: list[str] = []
    path = remote_dir.rstrip("/")
    while path and path not in {".", "/"}:
        parts.append(path)
        parent = str(Path(path).parent)
        if parent == path:
            break
        path = parent
    for directory in reversed(parts):
        try:
            sftp.stat(directory)
        except OSError:
            sftp.mkdir(directory)


def _upload_dir(sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str) -> None:
    _mkdir_p(sftp, remote_dir)
    for root, dirs, files in os.walk(local_dir):
        rel = Path(root).relative_to(local_dir)
        remote_root = remote_dir if rel == Path(".") else f"{remote_dir.rstrip('/')}/{rel.as_posix()}"
        _mkdir_p(sftp, remote_root)
        for name in dirs:
            _mkdir_p(sftp, f"{remote_root.rstrip('/')}/{name}")
        for name in files:
            local_file = Path(root) / name
            remote_file = f"{remote_root.rstrip('/')}/{name}"
            sftp.put(str(local_file), remote_file)
            print(f"已上传 {local_file} -> {remote_file}")


def _download_dir(sftp: paramiko.SFTPClient, remote_dir: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    for entry in sftp.listdir_attr(remote_dir):
        remote_item = f"{remote_dir.rstrip('/')}/{entry.filename}"
        local_item = local_dir / entry.filename
        if stat.S_ISDIR(entry.st_mode):
            _download_dir(sftp, remote_item, local_item)
        else:
            local_item.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(remote_item, str(local_item))
            print(f"已下载 {remote_item} -> {local_item}")


def resolve_password(args: argparse.Namespace) -> Optional[str]:
    if args.password:
        return args.password
    env_password = os.environ.get("SSH_PASSWORD")
    if env_password:
        return env_password
    if args.ask_password:
        return getpass.getpass(f"{args.user}@{args.host} 的密码: ")
    return None


def build_session(args: argparse.Namespace) -> SSHSession:
    return SSHSession(
        host=args.host,
        user=args.user,
        port=args.port,
        password=resolve_password(args),
        key_filename=args.key,
        timeout=args.timeout,
        look_for_keys=not args.no_keys,
        allow_agent=not args.no_agent,
        known_hosts=args.known_hosts,
        strict_host_key=not args.insecure,
    )


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="SSH 主机名或 IP")
    parser.add_argument("--user", default=os.environ.get("USER", "root"), help="登录用户名")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="SSH 端口，默认 22")
    parser.add_argument("--key", help="私钥路径，例如 ~/.ssh/id_rsa")
    parser.add_argument("--password", help="密码（不推荐，优先用 SSH_PASSWORD 或 --ask-password）")
    parser.add_argument("--ask-password", action="store_true", help="交互输入密码")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="连接/命令超时秒数")
    parser.add_argument("--known-hosts", help="known_hosts 文件路径")
    parser.add_argument("--insecure", action="store_true", help="不校验主机指纹（仅用于可信内网设备）")
    parser.add_argument("--no-keys", action="store_true", help="不自动搜索 ~/.ssh 下的私钥")
    parser.add_argument("--no-agent", action="store_true", help="不使用 ssh-agent")


def cmd_exec(args: argparse.Namespace) -> int:
    command = " ".join(args.command).strip()
    if not command:
        print("请提供要执行的命令", file=sys.stderr)
        return 2
    with build_session(args) as session:
        code, out, err = session.exec(command, timeout=args.timeout)
        if out:
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
        if err:
            sys.stderr.write(err)
            if not err.endswith("\n"):
                sys.stderr.write("\n")
        return code


def cmd_shell(args: argparse.Namespace) -> int:
    with build_session(args) as session:
        return session.interactive_shell()


def cmd_upload(args: argparse.Namespace) -> int:
    with build_session(args) as session:
        session.upload(args.local, args.remote)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    with build_session(args) as session:
        session.download(args.remote, args.local)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python SSH 客户端")
    sub = parser.add_subparsers(dest="action", required=True)

    exec_p = sub.add_parser("exec", help="执行远程命令")
    add_common_args(exec_p)
    exec_p.add_argument("command", nargs=argparse.REMAINDER, help="远程命令，建议用 -- 分隔")
    exec_p.set_defaults(func=cmd_exec)

    shell_p = sub.add_parser("shell", help="打开交互式 SSH 会话")
    add_common_args(shell_p)
    shell_p.set_defaults(func=cmd_shell)

    upload_p = sub.add_parser("upload", help="上传本地文件或目录")
    add_common_args(upload_p)
    upload_p.add_argument("local", help="本地路径")
    upload_p.add_argument("remote", help="远程路径")
    upload_p.set_defaults(func=cmd_upload)

    download_p = sub.add_parser("download", help="下载远程文件或目录")
    add_common_args(download_p)
    download_p.add_argument("remote", help="远程路径")
    download_p.add_argument("local", help="本地路径")
    download_p.set_defaults(func=cmd_download)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SSHError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
