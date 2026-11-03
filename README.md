# Virtuoso Optimizer

中文 | [English](#english)

## 中文

Cadence Virtuoso / spectre 电路参数优化工具：按目标自动改写原理图变量并重生成
网表，调用 spectre 迭代，支持多种搜索策略、多目标与断点续跑；同时提供无源器件
的版图生成与 EM 迭代。

### 运行

需要 Python 3.6+ 和图形桌面，本机可用 Virtuoso / spectre。收敛图需要
matplotlib，版图截图需要 Pillow（可选）。

```sh
python3 optimizer.py
```

在界面里按「环境 → 设计 → 变量 → 目标 → 迭代」填写后开始。没有 license 时可
用界面里的 mock 模式走通流程。

### AI skill：电感 PCell 几何守门（`.dsh/skills/virtuoso-pcell`）

仓库自带一个项目级 DeepSeek Harness skill，覆盖「读/改电感 PCell 的 `.il` 或
`lri/layout.py`」「版图图形看着对但面积/DRC/连通性不对」「把绘制逻辑讲清楚并出图」
这几类任务。它强制先判定真相源（SKILL `data/librf.il` 2251 行 vs Python 镜像），
再用零依赖检查器挡错：

```sh
python3 .dsh/skills/virtuoso-pcell/scripts/dump_geo.py -o geo.json      # 内核 → 几何
python3 .dsh/skills/virtuoso-pcell/scripts/check_geometry.py geo.json  # 13 条不变量
python3 .dsh/skills/virtuoso-pcell/scripts/draw_anatomy.py geo.json anatomy.png
```

几何 JSON 也可以由别的工具导出；检查器的 `--json` 输出供脚本消费。

### 离线传输（release/）

目标机不能拷贝文件、只能通过 VNC 剪贴板传文本时，`release/` 里的脚本把整个
程序包送过去：

1. 打开 `release/paste_001.sh`，全选复制，粘贴到目标机终端并回车；
2. 对 `release/paste_002.sh` 重复一次；最后一片会顺带生成 `check.sh` 和
   `unpack.sh`。
3. 执行 `sh check.sh && sh unpack.sh`，校验并解包到 `optimizer-1.7.3/`。

每片自带 base64 数据与校验和；`check.sh` 校验分片，`unpack.sh` 校验整包后
解包。RealVNC 剪贴板上限约 256 kB，超限会整段失败并粘出上一次的内容；若
`check.sh` 报某片不符，先复制任意小文本清空剪贴板，再重新复制该片。目标机
需要 `python3`、`base64`、`tar`、`xz`。

### 许可

MIT，见 `LICENSE`。

## English

Cadence Virtuoso / spectre parameter optimizer: rewrites schematic variables and
regenerates the netlist, drives spectre iterations with multiple search
strategies, multi-objective support and resumable runs; also generates passive
device layouts and iterates them with EM.

### Run

Python 3.6+, a graphical desktop, and a local Virtuoso / spectre installation.
matplotlib (convergence plots) and Pillow (layout screenshots) are optional.

```sh
python3 optimizer.py
```

Fill in Environment, Design, Variables, Targets and Iteration in the GUI, then
start. A mock mode is available when no license is present.

### AI skill: inductor PCell geometry gate (`.dsh/skills/virtuoso-pcell`)

The repo ships a project-level DeepSeek Harness skill for reading or changing the
inductor PCell (`.il` or `lri/layout.py`), for "the layout looks right but the
area / DRC / connectivity is wrong", and for explaining the drawing logic with
figures. It forces a truth-source decision first (`data/librf.il`, 2251 lines,
versus its Python mirror) and gates the change on a dependency-free checker:

```sh
python3 .dsh/skills/virtuoso-pcell/scripts/dump_geo.py -o geo.json
python3 .dsh/skills/virtuoso-pcell/scripts/check_geometry.py geo.json
python3 .dsh/skills/virtuoso-pcell/scripts/draw_anatomy.py geo.json anatomy.png
```

The geometry JSON may come from any producer; `--json` makes the checker
script-friendly.

### Offline transfer (release/)

When the target machine cannot receive files and only VNC clipboard text is
available, the scripts under `release/` carry the whole package over:

1. Open `release/paste_001.sh`, select all, copy, paste into the target terminal
   and press Enter;
2. Repeat with `release/paste_002.sh`; the last chunk also writes `check.sh`
   and `unpack.sh`;
3. Run `sh check.sh && sh unpack.sh` to verify and unpack into
   `optimizer-1.7.3/`.

Each chunk embeds its base64 payload and checksum. `check.sh` verifies the
chunks; `unpack.sh` verifies the archive and extracts it. The RealVNC clipboard
is limited to about 256 kB; exceeding it fails the paste and can leave the
previous content in place. If `check.sh` reports a bad chunk, copy a short
unrelated text to clear the clipboard and copy that chunk again. The target
needs `python3`, `base64`, `tar` and `xz`.

### License

MIT, see `LICENSE`.
