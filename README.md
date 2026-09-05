# Linux 多属性图像标注工具

本目录是从当前标注工具生成的 Linux 独立运行包，供后续人工标注使用。工具会递归读取数据目录中的图片，并在图片旁保存同名 UTF-8 JSON 标注文件。页面会实时发现新增、修改、移动和删除的图片，也可查看当前目录中 8 个属性的标注分布。

## 环境要求

- Linux
- Python 3.10 或更高版本（推荐 Python 3.11）
- 浏览器

## 安装

当前服务器上的项目目录为：

```text
/models/wangzhuo/01-code/03_deploy/label_mulAtti
```

首次安装执行：

```bash
cd /models/wangzhuo/01-code/03_deploy/label_mulAtti
/root/miniconda3/envs/wondron/bin/python3 -m pip install -r requirements.txt
chmod +x start.sh restart.sh
```

`restart.sh` 默认优先使用 `/root/miniconda3/envs/wondron/bin/python3`，找不到时使用 `python3`。它通过 `nohup` 在后台运行服务，SSH 断开后进程仍会继续运行。

复制或挂载数据后，可以先做只读检查：

```bash
python3 verify_package.py /data/label-data
```

## 当前服务器推荐方案

由于标注数据目录会经常变化，推荐使用：

```text
nohup 后台进程 → 固定入口 current_label_data → 当前实际数据目录
```

推荐把服务配置为读取项目下的 `current_label_data` 软链接。软件也提供网页文件夹选择器，可以像 Windows 资源管理器一样逐层点击 Linux 服务器上的目录，适合日常频繁更换标注位置，不依赖 Linux 服务器的图形桌面。外部程序向当前目录写入新图片后，页面通常会自动更新；即使文件系统监听不可用，也会通过周期校准补齐变化。

### 1. 创建当前数据目录软链接

把 `/实际/标注数据目录` 替换成图片和同名 JSON 所在的目录：

```bash
PROJECT_DIR=/models/wangzhuo/01-code/03_deploy/label_mulAtti
DATA_DIR=/实际/标注数据目录

test -d "$DATA_DIR"
ln -sfnT "$DATA_DIR" "$PROJECT_DIR/current_label_data"
readlink -f "$PROJECT_DIR/current_label_data"
```

`current_label_data` 必须是软链接；如果该位置已经存在真实文件或真实目录，`ln` 会失败，此时不要强制删除，应先检查里面是否有数据。

### 2. 创建服务环境配置

当前服务器允许网页选择 `/models/wangzhuo/01-code/03_deploy` 及其全部子目录：

```bash
sudo tee /etc/default/multi-attribute-label > /dev/null <<'EOF'
PYTHON_BIN=/root/miniconda3/envs/wondron/bin/python3
LABEL_DATA_DIR=/models/wangzhuo/01-code/03_deploy/label_mulAtti/current_label_data
LABEL_ALLOWED_DATA_ROOTS=/models/wangzhuo/01-code/03_deploy
LABEL_HOST=0.0.0.0
LABEL_PORT=8577
EOF
```

`LABEL_ALLOWED_DATA_ROOTS` 限制网页只能切换到该目录及其子目录，不要设置成 `/`。多个数据根目录使用英文冒号分隔，例如 `LABEL_ALLOWED_DATA_ROOTS=/models/wangzhuo/01-code/03_deploy:/data/label-tasks`。修改此配置文件后重新执行 `./restart.sh` 即可生效。

### 3. 使用 nohup 启动或重启

使用包内的 `restart.sh` 停止旧进程并通过 `nohup` 后台启动：

```bash
cd /models/wangzhuo/01-code/03_deploy/label_mulAtti
./restart.sh
```

也可以直接指定本次使用的数据目录：

```bash
./restart.sh /实际/标注数据目录
```

脚本把 PID 写入 `annotation.pid`，日志写入 `annotation.log`，并检查 `/api/v1/health`。首次从旧版本迁移时，如果检测到原来的 systemd 服务，脚本会请求 `sudo` 并一次性将其停用，以免占用同一端口。后续启动不需要 `sudo`。

验证当前实际读取的数据目录：

```bash
curl -fsS http://127.0.0.1:8577/api/v1/config | python3 -m json.tool
```

返回结果中的 `data_dir` 应当是软链接指向的实际数据目录；`allowed_data_roots` 应当只包含允许网页浏览的公共数据根目录。

### 4. 日常切换标注数据

最方便的方式是在网页右上角点击“切换文件夹”，然后在弹窗中选择目录：

1. 单击文件夹将其选中。
2. 双击文件夹或选中后按 `Enter` 进入下一层。
3. 使用“上一级”、面包屑或“刷新”浏览目录。
4. 确认“当前选择”正确后，点击“选择此文件夹”。

弹窗也保留了“地址”输入栏；已知服务器绝对路径时，可直接输入 `/models/wangzhuo/01-code/03_deploy/label-task-20260813` 并点击“转到”。

目录必须已经存在，位于 `LABEL_ALLOWED_DATA_ROOTS` 配置的根目录内，并且运行服务的用户拥有读取、写入和进入目录的权限。这里输入的是服务器路径，不是操作电脑上的本地路径。

切换对当前运行的服务进程全局生效。其他已经打开的标注页面会停止读写并提示刷新，防止写入错误目录；因此切换前应确认所有页面的当前标注均已保存。

网页切换不会修改 `current_label_data` 软链接或 `/etc/default`，所以服务重启后会回到配置的初始目录。需要让新目录在重启后仍然生效时，请使用下面的命令行方式更新软链接。

也可以在服务器命令行更新软链接并重启服务：

```bash
PROJECT_DIR=/models/wangzhuo/01-code/03_deploy/label_mulAtti
NEW_DATA_DIR=/新的/标注数据目录

# 确认新目录存在且当前用户可以读写
test -d "$NEW_DATA_DIR"
test -r "$NEW_DATA_DIR"
test -w "$NEW_DATA_DIR"

# 更新固定入口，然后重启服务
ln -sfnT "$NEW_DATA_DIR" "$PROJECT_DIR/current_label_data"
readlink -f "$PROJECT_DIR/current_label_data"
"$PROJECT_DIR/restart.sh"

# 确认服务和实际数据目录
ps -fp "$(cat "$PROJECT_DIR/annotation.pid")"
curl -fsS http://127.0.0.1:8577/api/v1/config | python3 -m json.tool
```

重启后，浏览器刷新页面即可载入新目录。不要在有人尚未保存标注时切换目录。

如果所有标注数据都位于同一个规模不大的公共父目录下，也可以直接把软链接指向这个父目录。工具会递归读取其子目录，这样新增子目录后通常只需刷新浏览器，不必切换服务；但父目录图片很多时，首次扫描和页面加载会变慢。

### 5. 访问方式

服务默认监听 `0.0.0.0:8577`。多台电脑可以直接在浏览器中访问：

```text
http://118.31.105.171:8577/
```

服务器安全组和防火墙需要放行 TCP 8577。建议仅允许需要使用标注工具的固定公网 IP 或可信内网网段访问。

确认 `/etc/default/multi-attribute-label` 中包含：

```text
LABEL_HOST=0.0.0.0
LABEL_PORT=8577
```

环境配置修改后重新执行启动脚本：

```bash
./restart.sh
```

此工具没有登录鉴权。不要向整个公网（`0.0.0.0/0`）放行 8577，否则任何能连接该端口的人都可能浏览或修改标注数据。

## 启动

把待标注图片放入任意目录。已有标注时，把 JSON 与对应图片放在同一目录，并保持文件名主体一致：

```text
/data/label-data/
├── 子目录/
│   ├── food_001.jpg
│   └── food_001.json
└── food_002.png
```

指定该目录启动：

```bash
LABEL_HOST=0.0.0.0 LABEL_PORT=8577 ./start.sh /data/label-data
```

没有传数据目录时，默认使用本工具包中的空目录 `10-temp_label`：

```bash
./start.sh
```

浏览器访问：

```text
http://118.31.105.171:8577/
```

从其他电脑访问时使用同一地址。若无法连接，请确认云服务器安全组和系统防火墙已对这些电脑的来源 IP 放行 TCP 8577。

可用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PYTHON_BIN` | `python3` | Python 命令 |
| `LABEL_DATA_DIR` | `10-temp_label` | 未传第一个参数时使用的数据目录 |
| `LABEL_HOST` | `0.0.0.0` | 监听所有服务器网卡，供其他电脑连接 |
| `LABEL_PORT` | `8577` | 服务端口 |
| `LABEL_ALLOWED_DATA_ROOTS` | 空（仅初始目录） | 网页允许切换的数据根目录；多个路径用 `:` 分隔。空值不会开放整个文件系统 |

也可以绕过启动脚本直接运行：

```bash
python3 run_annotation_ui.py \
  --data-dir /data/label-data \
  --host 0.0.0.0 \
  --port 8577 \
  --no-browser
```

## nohup 进程管理

启动或重启统一执行：

```bash
cd /models/wangzhuo/01-code/03_deploy/label_mulAtti
./restart.sh
```

`restart.sh` 会安全核对 `annotation.pid` 指向的进程，停止旧实例，再使用 `nohup` 启动新实例。退出当前 SSH 或终端后服务仍会继续运行；但服务器重启或进程异常退出后，`nohup` 不会自动恢复。

查看进程、健康状态和实时日志：

```bash
ps -fp "$(cat annotation.pid)"
curl -fsS http://127.0.0.1:8577/api/v1/health
tail -f annotation.log
```

停止服务：

```bash
PID="$(cat annotation.pid)"
ps -fp "$PID"
kill "$PID"
rm -f annotation.pid
```

若端口仍被其他进程占用，可检查：

```bash
ss -ltnp | grep ':8577'
```

启动后可在服务器上检查健康状态：

```bash
curl -fsS http://127.0.0.1:8577/api/v1/health
```

## 下载与删除

- 左侧“下载已标注数据”将当前文件夹及其子目录中的已保存原图和 `标注数据.xlsx` 打包为 `当前文件夹名称.zip`。Excel 每张图片一行，按照片文件名、食物名称、食物数量、总重量（g）、设备型号、容器类型、附件类型、层位、食物尺寸分列；多选值用“、”分隔，空值留空，子目录同名照片用相对路径区分。未保存的表单修改不计入导出。
- 没有可导出的标注时提示“暂无已标注数据可下载”；异常 JSON 会提示原因，修复后可重试。
- 预览下方“删除当前图像”或快捷键 **D** 都会先显示照片名及永久删除警告，确认后从磁盘删除原图和同名 JSON。输入框、选择框和可编辑区域中的 D 不触发删除。
- 删除后自动切到后面的照片；最后一张删除后显示空状态。失败时显示原因并核对磁盘状态；多个扩展名的图片共用一份 JSON 时拒绝删除。
- 新照片经过稳定性和完整解码检查后自动加入列表。实时事件前端合并等待为 80ms；服务端无监听器时每 0.5 秒扫描，监听器漏报每 1 秒校准；浏览器事件连接中断时每 2 秒同步。实际速度受磁盘、网络及图像大小影响。同步保留当前选择、表单草稿和列表滚动位置。

更新后安装依赖并重启服务，以启用 Excel 导出和图片完整性检查：

```bash
python3 -m pip install -r requirements.txt
./restart.sh
```

开发验证：`python3 -m unittest discover -s tests -v`，以及 `node --test tests/test_frontend.cjs`。

## 标注文件格式

支持的图片扩展名为 `.bmp`、`.gif`、`.jpeg`、`.jpg`、`.png`、`.tif`、`.tiff`、`.webp`。保存后的 JSON 结构如下：

```json
{
  "image": "food_001.jpg",
  "image_name": "food_001.jpg",
  "image_root": "/data/label-data/子目录",
  "annotation_version": "1.6",
  "updated_at": "2026-08-13T00:00:00.000Z",
  "annotations": {
    "food_name": "包子",
    "food_count": 8,
    "quality": 663.7,
    "device_model": "C9277A",
    "container_type": ["无"],
    "accessory_type": ["烤盘"],
    "rack_level": ["5"],
    "food_size": 10
  }
}
```

设备型号可选值为 `C9277A`、`CQ09-i9`、`C87-i7Pro`、`DB677`。工具兼容读取历史标注；在 Linux 上重新保存后，`image_root` 会自动写成图片所在的实际 Linux 目录。

## Linux 服务器注意事项

- 更新程序并重启服务后，如果页面仍出现 `Field required`，说明浏览器保留了旧版 JavaScript。请按 `Ctrl+F5` 强制刷新一次；新版服务会禁止缓存页面资源，后续更新不会继续使用旧文件。
- 首次启动时通过 `./start.sh /绝对数据路径`、`LABEL_DATA_DIR` 或 `--data-dir` 指定初始目录；启动后可以在网页中继续切换。
- 页面中的“切换文件夹”使用网页内的服务器目录浏览器：单击选择、双击进入，也可在地址栏填写服务器绝对路径。无需 Linux 图形桌面，并受 `LABEL_ALLOWED_DATA_ROOTS` 限制；未配置时只允许初始数据目录，不会浏览整个服务器。
- 新图片写入完成后会自动出现在列表中；`watchdog` 不可用或网络文件系统漏报事件时，服务仍会周期扫描校准。右上角“属性统计”只汇总已经保存且格式有效的 JSON，当前未保存表单和异常 JSON 不计入属性分布。
- 保存 JSON 时使用同目录原子替换；覆盖已有 JSON 会保留其权限位，新建 JSON 遵循服务进程的 `umask`，便于共享组数据集继续协作。
- 此工具本身不提供登录鉴权。当前配置监听 `0.0.0.0:8577` 供多台电脑连接，必须通过云安全组和系统防火墙限制允许访问的来源 IP，不能向整个公网开放。
- `image_root` 只记录保存时图片所在目录，不用于定位图片，所以历史 JSON 中保留 Windows 路径也不影响读取。
- 层位应与设备型号匹配：`C9277A` 和 `C87-i7Pro` 可选 `0`（底板层）及 `1`–`5`；`CQ09-i9` 可选 `0` 及 `1`–`3`；`DB677` 只能选 `1`、`2`。设备型号为空时，层位应选“无”。当前页面不会自动拦截不匹配组合，保存前需人工确认，否则后续训练数据生成会跳过该标注。

## 当前标注数据

生成本工具包时，项目的 `00-dataset` 中共有 4554 组已配对的图片和 JSON，且全部可被当前标注工具读取。数据量约 1.15 GB，未重复复制进本工具包。需要继续修改这批标注时，可将整个 `00-dataset` 复制或挂载到 Linux，然后把它作为启动参数：

```bash
./start.sh /data/06-多属性/00-dataset
```
