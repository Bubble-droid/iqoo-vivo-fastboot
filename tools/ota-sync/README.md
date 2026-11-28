# ota-sync — 在更新器下载时抢救完整 OTA 包

vivo / iQOO 的系统更新器下载完整包时，会先建一个**预分配（稀疏）文件**，再把文件切成若干段并行写入。下载完成后更新器会校验、重启进 Recovery 并删除安装包——等它“下完再拷”往往就来不及了。

本工具利用这段窗口：

```text
手机开始下载 → 有进度后暂停 → 主机运行本工具
    → 自动探测路径 / 数据库 / 总大小 / 分段数 / 服务器 MD5
    → 建立硬链接与断点文件 → 提示“就绪”
    → 手机继续下载 → 主机增量同步 → 完成瞬间冻结更新器
    → 设备 MD5 + 服务器 MD5/SHA-256 + ZIP CRC 校验
    → 输出到 ~/Downloads
```

得到的 zip 可直接用仓库根目录的 [`ota2fastboot`](../../README.md) 转成线刷包。

## 依赖

- Python 3.8+（仅标准库）
- `adb` 在 `PATH` 中（USB 或无线调试均可）
- 手机已 root（KernelSU / Magisk），并已给 `adb shell` 授权
- 约 5–6 GiB 本机空闲空间（与 OTA 等大）

## 快速开始

```bash
# 1. 手机：设置 → 系统升级 → 开始下载，等进度出现后暂停
# 2. 主机：
./tools/ota-sync/ota-sync

# 看到“已就绪”后，在手机上点继续下载
```

多台设备时：

```bash
./tools/ota-sync/ota-sync --serial 343183322000105
# 或
ANDROID_SERIAL=343183322000105 ./tools/ota-sync/ota-sync
```

先看探测结果、不传输：

```bash
./tools/ota-sync/ota-sync --detect-only
./tools/ota-sync/ota-sync --dump-db      # 打印 downloads.db 表结构与最新一行
```

## 自动探测的内容

| 项目 | 来源（按优先级） |
| --- | --- |
| 下载数据库 | `--db` → 已知路径 → 在 `/data/user_de`、`/data/data` 下查找 `downloads.db` |
| OTA 文件 | `--path` → `downloads._data` → `remote.xml` 的 `fullFilePath` → 扫描 `/data/vivo-updater/<包名>/OTAPackage` → 保命硬链接 |
| 更新器包名 | `--package` → 从数据库路径或 OTA 路径反推 |
| 总大小 | `--total` → `downloads.total_bytes` → `remote.xml totalBytes` → `update_info.xml fileLength` → HTTP `Content-Length` |
| 分段数 | `--segments` → `downloads.download_type` → 下载库默认 5（小于 1 MiB 时为 1） |
| 服务器 MD5 | `--md5` → HTTP `x-kss-meta-md5` → `downloads.check_sum` |
| 服务器 SHA-256 | `--sha256` → `update_info.xml` 的 `sha256` |

分段边界复刻下载库 `com.vivo.ic.dm` 的算法（`download_type` 即并行线程数）：

```text
chunk = total // count + 1
第 k 段 = [k * chunk, (k + 1) * chunk)，最后一段以 total 收尾
```

所有值都从设备记录、文件本身或 HTTP 响应读取，没有写死机型、序列号、路径或大小。

## 安全机制

1. **硬链接**：在 `/data/local/tmp/ota-keep-<文件名>` 和 `/data/local/ota-keep-<文件名>` 各建一个硬链接。OTA 流程不格式化 `/data`，即使更新器删掉原路径，inode 仍在。
2. **设备端看门狗**：`device/freeze_watch.sh` 轮询更新器的读取突发与文件分配量；判定下载完成/开始整包校验时，立即对所有更新器进程 `kill -STOP`，并写标记文件。
3. **断点续传**：本机 `.part` 文件 + `.part.state.json` 记录每段已拷位置；中断后重跑会接着传。
4. **三重校验**：本机 MD5 对比设备 MD5（设备端 `md5sum`），再对比服务器 MD5 / SHA-256，最后做 ZIP CRC 校验；不一致会自动全量重拷一次。
5. **块级探测**：每段只信任“已确认写完”的 64 KiB 块，正在写的边界块留给下一轮，避免把零填充当成数据。

## 常用选项

| 选项 | 说明 |
| --- | --- |
| `--serial` | 指定 adb 设备；默认自动选择唯一在线设备 |
| `--path` | 手动指定设备上的 OTA 文件 |
| `--db` | 手动指定 `downloads.db` |
| `--package` | 手动指定更新器包名 |
| `--total` | 总字节数，支持 `5284163108`、`5GiB` |
| `--segments` | 并行分段数 |
| `--md5` / `--sha256` | 期望的服务器摘要 |
| `--url` | 下载 URL（用于读取服务器元数据） |
| `--dest` | 本机输出目录，默认 `~/Downloads` |
| `--keep` | 设备端保命硬链接路径 |
| `--prepare-only` | 只建链接和断点文件，不开始同步 |
| `--detect-only` | 只打印探测结果 |
| `--dump-db` | 打印数据库结构与最新一行 |
| `--no-watch` | 不启动设备端看门狗（仍在完成时尝试冻结） |
| `--no-freeze` | 完全不冻结更新器（调试用） |
| `--yes` | 不等待回车，立即开始同步 |

## 完成之后

工具结束时更新器处于 `SIGSTOP` 冻结状态，OTA 文件仍在设备上。若还想让手机自己升级：

```bash
adb shell 'for p in $(pidof com.bbk.updater) $(pidof com.bbk.updater:remote); do kill -CONT $p; done'
```

若不打算升级，直接删除设备上的保命链接即可：

```bash
adb shell 'rm -f /data/local/tmp/ota-keep-*.zip /data/local/ota-keep-*.zip'
```

> 不要删除正在下载的原始文件，除非确认已不再需要。

## 实现说明

- `ota_sync/adb.py`：只用 `adb` 命令行，USB / 无线一致；自动探测 `su`（KernelSU、Magisk、常见路径）。
- `ota_sync/detect.py`：数据库、`shared_prefs`、HTTP HEAD、分段数学。
- `ota_sync/session.py`：同步主循环、看门狗、收尾、校验。
- `ota_sync/state.py`：断点状态。
- `device/probe_bounds.sh`：设备端块级前缀探测。
- `device/freeze_watch.sh`：设备端冻结看门狗。
- 尺寸格式化、`parse_size`、SHA-256 复用 `ota2fastboot.util`，避免重复实现。

设备 `mksh` 的 `$(( ))` 与 `[ ]` 是 32 位有符号，5 GB 级字节数会溢出，所以设备脚本一律用 64 KiB 块号或 `awk` 处理大数；`dd iflag=skip_bytes` 负责大于 4 GiB 的偏移。

## 测试

```bash
python3 tools/ota-sync/tests/test_ota_sync.py
```

覆盖分段布局、探测优先级、数据库解析、断点状态、块边界与 ZIP 校验，不需要连接设备。

实机流程（探测、增量同步、冻结、校验）已在 iQOO 9 Pro（PD2172）上验证。
