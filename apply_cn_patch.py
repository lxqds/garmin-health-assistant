#!/usr/bin/env python3
"""为 garminconnect 0.3.2 打中国区(CN)登录补丁。

用法: python apply_cn_patch.py [garminconnect包路径]
默认路径: <venv>/lib/python3.11/site-packages/garminconnect/client.py

背景: 0.3.2 的 CN 修复写在 __init__.py 但作用于 client.py 的模块级常量,
从未生效。上游 PR #360(域感知 DI 端点)与 PR #363(CN 跳过 mobile)的等价补丁。
升级到 0.3.12+(需 Python≥3.12)后无需此补丁。
"""
import sys
from pathlib import Path

def find_client_py() -> Path | None:
    import garminconnect
    pkg_dir = Path(garminconnect.__file__).resolve().parent
    cand = pkg_dir / "client.py"
    return cand if cand.exists() else None

def apply(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    orig = src
    n = 0

    # 补丁 A: __init__ 加 _di_token_url(域感知)
    anchor_a = 'self._portal_service_url = f"https://connect.{domain}/app"'
    patch_a = anchor_a + '''
        # DI auth host is domain-aware too — CN users live on diauth.garmin.cn
        # and don't exist in the .com user database (fix ported from 0.3.6).
        self._di_token_url = f"https://diauth.{domain}/di-oauth2-service/oauth/token"'''
    if anchor_a in src and "_di_token_url" not in src:
        src = src.replace(anchor_a, patch_a, 1)
        n += 1

    # 补丁 B: login() 跳过 mobile 策略(CN 域)
    anchor_b = '            ("portal+requests",\n                lambda: self._portal_web_login_requests(email, password),\n            ),\n        ]'
    patch_b = anchor_b + '''
        # CN (and other non-.com) domains: mobile.integration.garmin.com does
        # not exist for them — skip mobile strategies (fix from upstream #363).
        if self.domain != "garmin.com":
            strategies = [
                (n, fn) for n, fn in strategies
                if not n.startswith("mobile")
            ]'''
    if anchor_b in src and "skip mobile login strategies" not in src:
        src = src.replace(anchor_b, patch_b, 1)
        n += 1

    # 补丁 C+D: 两处 DI_TOKEN_URL -> self._di_token_url
    for old, new in [
        ("di_token_url = DI_TOKEN_URL", "di_token_url = self._di_token_url"),
    ]:
        cnt = src.count(old)
        if cnt:
            src = src.replace(old, new)
            n += cnt

    if src == orig:
        print("无需补丁或已打过,跳过。")
        return False
    path.write_text(src, encoding="utf-8")
    print(f"✅ 已应用 {n} 处补丁 → {path}")
    return True

if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if p is None:
        p = find_client_py()
    if p is None:
        print("找不到 garminconnect/client.py,请传入路径", file=sys.stderr)
        sys.exit(1)
    apply(p)
