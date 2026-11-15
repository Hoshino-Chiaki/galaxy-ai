#!/usr/bin/env bash
set -euo pipefail
# The upstream source is pinned and never patched; wrapper and binary stay in feishu/.
feishu_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$feishu_dir" != /opt/galaxy-ai/backend/feishu ]]; then
  echo '飞书服务端 CLI 仅在服务器 /opt/galaxy-ai/backend/feishu 构建。' >&2
  exit 1
fi
feishu_commit=ac22a60a23d66c37d43a7ec6d9e99b99a4320356
feishu_build_dir="$(mktemp -d)"
trap 'rm -rf -- "$feishu_build_dir"' EXIT
git clone --quiet https://github.com/larksuite/cli.git "$feishu_build_dir/upstream"
git -C "$feishu_build_dir/upstream" checkout --quiet "$feishu_commit"
cp "$feishu_dir/cli/"*.go "$feishu_build_dir/"
cp "$feishu_dir/cli/allowed-commands.json" "$feishu_build_dir/"
cd "$feishu_build_dir"
go mod init galaxy-ai/feishu-wrapper
go mod edit -go 1.25
go mod edit -replace "github.com/larksuite/cli=$feishu_build_dir/upstream"
go mod tidy
mkdir -p "$feishu_dir/bin"
CGO_ENABLED=0 go build -trimpath -o "$feishu_dir/bin/lark-cli" .
cp "$feishu_build_dir/upstream/LICENSE" "$feishu_dir/cli/UPSTREAM-LICENSE"
printf '%s\n' "$feishu_commit" > "$feishu_dir/cli/upstream.commit"
