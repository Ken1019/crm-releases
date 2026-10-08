"""更新情報（latest.json）を作る。リリースのときに実行する。

    python packaging/make_manifest.py <版> <インストーラーのパス> <出力先> [リポジトリ]

インストール済みのGHMSは、この latest.json を見て新しい版があるか確認し、
sha256 が一致したインストーラーだけを使って更新する。
"""
import hashlib
import json
import os
import re
import sys
from datetime import date


def notes_for(version, changelog="CHANGELOG.md"):
    """CHANGELOG.md の「## 版」の見出しから次の見出しまでを取り出す"""
    try:
        text = open(changelog, encoding="utf-8").read()
    except OSError:
        return ""
    m = re.search(rf"^## +v?{re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def main():
    version, installer, out = sys.argv[1], sys.argv[2], sys.argv[3]
    repo = sys.argv[4] if len(sys.argv) > 4 else "Ken1019/crm-releases"
    h = hashlib.sha256()
    with open(installer, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    name = os.path.basename(installer)
    manifest = {
        "version": version,
        "installer": f"https://github.com/{repo}/releases/download/v{version}/{name}",
        "sha256": h.hexdigest(),
        "notes": notes_for(version),
        "date": date.today().isoformat(),
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
