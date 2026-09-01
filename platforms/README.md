# platforms — 单仓库双目录

- `win64/scripts/` — Windows 平台启动器（bat）
- `linux/scripts/` — Linux/macOS 平台启动器（sh + service）

构建时 `scripts/build_release.py --platform win64|linux` 会优先从对应目录取启动器，
回退 `scripts/` 以兼容开发期直接编辑。
