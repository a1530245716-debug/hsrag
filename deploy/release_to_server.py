# -*- coding: utf-8 -*-
"""一键发版到生产服务器（Windows 本机运行，依赖 paramiko）。

服务器：root@10.20.154.26:/opt/rag
流程：备份 .env/config → 上传源码包 → 解压换 src → docker build →
      切 RAG_TAG → compose up -d --pull never → health 检查。

用法（在 rag 目录下）：
    python deploy/release_to_server.py \
        --tag 20261126-feedback \
        --src dist/rag-src-20261126-feedback.tar.gz

密码：交互输入（getpass），不会写入磁盘/日志。
也可用环境变量 RAG_DEPLOY_PASS 传入（CI/临时使用，注意 shell 历史）。
"""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK，输出 ✅/❌/中文混合会 UnicodeEncodeError；
# 统一成 UTF-8，失败时也不因打印把发版结果带崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import paramiko
except ImportError:
    print("缺少 paramiko：pip install paramiko", file=sys.stderr)
    raise SystemExit(2)

HOST = "10.20.154.26"
USER = "root"
REMOTE_ROOT = "/opt/rag"
COMPOSE = "/opt/rag/compose.yml"
HEALTH = "http://127.0.0.1:8000/api/health"


def log(msg):
    print(msg, flush=True)


def run(ssh, cmd, title=None):
    """执行远程命令并实时打印输出，返回 (exit_code, output)。"""
    if title:
        log(f"\n=== {title} ===")
    log(f"$ {cmd}")
    chan = ssh.get_transport().open_session()
    chan.set_combine_stderr(True)
    chan.exec_command(cmd)
    out = []
    while True:
        data = chan.recv(4096)
        if not data:
            break
        text = data.decode("utf-8", "replace")
        out.append(text)
        sys.stdout.write(text)
        sys.stdout.flush()
    code = chan.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"远程命令失败（exit {code}）：{cmd}")
    return code, "".join(out)


def upload(sftp, local: Path, remote: str):
    log(f"\n=== 上传 {local.name} ({local.stat().st_size/1024/1024:.1f} MB) ===")
    last = [-1]

    def progress(done, total):
        pct = int(done * 100 / total) if total else 0
        if pct >= last[0] + 10:
            last[0] = pct
            log(f"  {pct}%  ({done/1024/1024:.1f}/{total/1024/1024:.1f} MB)")
    sftp.put(str(local), remote, callback=progress)
    log("上传完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--user", default=USER)
    ap.add_argument("--tag", required=True, help="镜像 tag，如 20261126-feedback")
    ap.add_argument("--src", required=True, help="本地源码包（含 deploy/wheels）")
    ap.add_argument("--timeout", type=int, default=1800, help="构建/重启总超时秒数")
    ap.add_argument("--skip-release-check", action="store_true",
                    help="跳过 app/release_notes.json 的版本校验（仅紧急情况）")
    args = ap.parse_args()

    local = Path(args.src)
    if not local.is_file():
        print(f"源码包不存在：{local}", file=sys.stderr)
        return 2
    tag = args.tag.strip()
    if not tag or "/" in tag or " " in tag:
        print("tag 不合法", file=sys.stderr)
        return 2

    # 每次上生产都必须有一次面向所有用户的更新说明；版本号与实际 tag 对齐，
    # 防止发版后弹窗还显示上一次的内容。紧急情况可用 --skip-release-check。
    if not args.skip_release_check:
        notes_path = Path(__file__).resolve().parent.parent / "app" / "release_notes.json"
        try:
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"读不到更新说明 {notes_path}: {e}", file=sys.stderr)
            return 2
        if str(notes.get("version") or "") != tag:
            print(f"发版前请先更新 {notes_path}：version 应为本次 tag {tag!r}，"
                  f"当前是 {notes.get('version')!r}；items 写这次用户能感知的变化。",
                  file=sys.stderr)
            return 2
        if not isinstance(notes.get("items"), list) or not notes.get("items"):
            print(f"{notes_path} 的 items 不能为空", file=sys.stderr)
            return 2

    pwd = os.environ.get("RAG_DEPLOY_PASS")
    if not pwd:
        if not sys.stdin.isatty():
            print("需要密码：请在本机终端运行，或设置 RAG_DEPLOY_PASS（注意 shell 历史）。",
                  file=sys.stderr)
            return 2
        pwd = getpass.getpass(f"{args.user}@{args.host} 密码（不回显）: ")
    if not pwd:
        print("密码为空", file=sys.stderr)
        return 2

    ssh = paramiko.SSHClient()
    # 优先用本机 known_hosts 校验主机指纹；未知主机才临时接受并提示
    kh = Path(os.path.expanduser("~/.ssh/known_hosts"))
    if kh.exists():
        ssh.load_host_keys(str(kh))
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log(f"连接 {args.user}@{args.host} …")
    ssh.connect(args.host, username=args.user, password=pwd,
                timeout=15, banner_timeout=20, auth_timeout=20)
    ssh.get_transport().set_keepalive(15)   # 构建耗时较长，防止空闲被断开
    log("已连接")

    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{REMOTE_ROOT}/backup/{tag}-{ts}"
    remote_tar = f"{REMOTE_ROOT}/upload/rag-src-{tag}.tar.gz"
    src_new = f"{REMOTE_ROOT}/src.new.{ts}"
    src_old = f"{REMOTE_ROOT}/src.bak-{ts}"

    try:
        run(ssh, "hostname && docker --version && cd /opt/rag && "
                 "docker-compose -f compose.yml ps 2>&1 | tail -5",
            "服务器与当前容器")

        run(ssh, f"set -e; mkdir -p '{backup}' '{REMOTE_ROOT}/upload'; "
                 f"cp -a '{REMOTE_ROOT}/.env' '{backup}/env.bak' 2>/dev/null || true; "
                 f"cp -a '{REMOTE_ROOT}/config.json' '{backup}/config.json.bak' 2>/dev/null || true; "
                 f"docker image ls rag-bff --format '{{{{.Tag}}}}' | head -10 > '{backup}/images.txt'; "
                 f"cat '{REMOTE_ROOT}/.env' || true; echo backup={backup}",
            "备份 .env / config / 镜像列表")

        with ssh.open_sftp() as sftp:
            upload(sftp, local, remote_tar)

        run(ssh, f"set -e; rm -rf '{src_new}'; mkdir -p '{src_new}'; "
                 f"tar xzf '{remote_tar}' -C '{src_new}'; "
                 f"test -f '{src_new}/deploy/Dockerfile'; "
                 f"test -d '{src_new}/deploy/wheels'; "
                 f"test ! -d '{src_new}/app/data'; "
                 f"test ! -f '{src_new}/app/config.json'; "
                 f"echo '源码包校验通过（无 data/config，含 wheels）'",
            "解压并校验源码包")

        run(ssh, f"set -e; rm -rf '{src_old}'; mv '{REMOTE_ROOT}/src' '{src_old}'; "
                 f"mv '{src_new}' '{REMOTE_ROOT}/src'; echo old={src_old}",
            "切换源码目录")

        run(ssh, f"set -e; cd '{REMOTE_ROOT}/src'; "
                 f"docker build -f deploy/Dockerfile -t 'rag-bff:{tag}' .",
            f"构建镜像 rag-bff:{tag}")

        run(ssh, f"set -e; docker run --rm 'rag-bff:{tag}' sh -c "
                 f"'test ! -d /app/app/data && test ! -f /app/app/config.json && echo image_ok'",
            "校验镜像不含数据/配置")

        run(ssh, f"set -e; printf 'RAG_TAG={tag}\\n' > '{REMOTE_ROOT}/.env'; "
                 f"cd '{REMOTE_ROOT}'; docker-compose -f compose.yml up -d --pull never",
            "切换 RAG_TAG 并重建容器")

        run(ssh, f"set -e; for i in $(seq 1 90); do "
                 f"code=$(curl -s -o /dev/null -w '%{{http_code}}' {HEALTH} || true); "
                 f"if [ \"$code\" = 200 ]; then echo health_200_after_${{i}}0pct; break; fi; "
                 f"[ $i -eq 90 ] && echo health_timeout; sleep 2; done; "
                 f"curl -s {HEALTH}; echo; "
                 f"docker ps --filter name=rag-bff --format '{{{{.Status}}}}'; "
                 f"docker exec rag-bff grep -c '反馈 / 提需求' /app/app/static/index.html; "
                 f"curl -s -o /dev/null -w 'suggestions_http=%{{http_code}}\\n' "
                 f"http://127.0.0.1:8000/api/suggestions",
            "健康检查与新功能校验")

        log("\n[OK] 发版完成")
        log(f"   镜像：rag-bff:{tag}")
        log(f"   备份：{backup}（.env / config / 镜像列表）")
        log(f"   旧源码：{src_old}")
        log("   回滚：改 /opt/rag/.env 的 RAG_TAG 为上一条 tag，"
            "再 docker-compose -f /opt/rag/compose.yml up -d --pull never")
        return 0
    except Exception as e:
        log(f"\n[FAIL] 发版失败：{e}")
        log("   若已切换 src/tag 但容器异常，可按上面备份目录回滚。")
        return 1
    finally:
        ssh.close()


if __name__ == "__main__":
    raise SystemExit(main())
