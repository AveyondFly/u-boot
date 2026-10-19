RK3562 板级构建与关机充电
========================

1. 三种 DTB 的关系

u-boot.dtb 是各机型独立生成的早期控制 DTB，已作为 FIT 的 fdt 节点打包在
uboot.img 中；不用把单独的 u-boot.dtb 拷贝到 /flash。SPL 使用同一机型 DTS
裁剪生成的 u-boot-spl.dtb，再应用原厂 SPL 的 MMC 阶段差异。两台设备不再
选择共用的 rk3562-evb 控制树。

rk3562-charge.dtb 用于 U-Boot 初始化板级 PMIC、电量计、按键和充电灯。
它以该板原厂 Linux DTB 为基础应用板级 overlay，与早期控制 DTB 分开管理。

Linux 的 DTB 仍由 extlinux.conf 的 FDT 指定。当前 NGP45H 为
/device_trees/rk3562-ngp45h.dtb，不因充电构建而改动。

默认只构建 embedded 内嵌模式，输出分目录保存：
  embedded：充电 DTB 打包为 uboot.img 的 kern-fdt 节点，并强制使用内嵌版本。
            不需要外部 /flash/rk3562-charge.dtb。单独导出的 DTB 用于检查。
  external：保留为显式选择的调试模式。U-Boot 启动时从启动介质可启动分区读取
            /rk3562-charge.dtb，当前 TF 卡下对应 /flash/rk3562-charge.dtb。

两种模式都包含早期控制 DTB。内嵌模式仍需要 extlinux 指定的 Linux DTB。
更改外部模式的充电 DTB 可以单独更新文件；更改内嵌模式的充电 DTB 需要重打包
uboot.img。内嵌 DTB 限制为 512 KiB，对应现有 U-Boot 分配的缓冲区大小。

2. 共用构建入口

  ./build_uboot_rk3562_board.sh --list-boards
  ./build_uboot_rk3562_board.sh --board ngp45h
  ./build_uboot_rk3562_board.sh --board ngp45h --dtb-only
  ./build_uboot_rk3562_board.sh --board pocket-ais

兼容入口 ./build_uboot_rk3562_charge.sh 等价于选择 ngp45h，接受相同参数。
原 build-rk3562-charge/ 中的旧产物保留，不会由新入口覆盖。

默认使用：
  /opt/toolchains/gcc-linaro-6.3.1-2017.05-x86_64_aarch64-linux-gnu/bin/aarch64-linux-gnu-
可通过 CROSS_COMPILE、JOBS 覆盖工具链前缀、并行度。
--dtb-only 只依赖 dtc/fdtoverlay/fdtget，不调用交叉编译器或 rkbin。

目录约定：
  board/rockchip/rk3562-profiles/<board>/board.json  版本化板级配置
  board/rockchip/rk3562-profiles/<board>/charge.dtso 板级充电 overlay
  board/rockchip/rk3562-profiles/<board>/source/     可选 U-Boot 源码补充
  board-data/rk3562/<board>/                       原厂只读提取输入
  build-rk3562-boards/<board>/external/             外部 DTB 版完整产物
  build-rk3562-boards/<board>/embedded/             内嵌 DTB 版完整产物
  build-rk3562-boards/<board>/dtb/                  单独编译 DTB 的产物

board-data 中的设备输入、诊断记录和刷写备份仅保留在本地，不提交 Git。
新 checkout 需要先提取原厂资料或指定 --input-dir。rkbin 的固定版本和校验和
纳入版本控制，归档仅作为本地缓存；首次构建缺少缓存时会下载并校验固定版本。
SFTP 提取脚本依赖 Python paramiko；构建使用 Python 3.9 或更新版本及
dtc、fdtoverlay、fdtget、Git、tar、curl 和指定交叉编译器。

--input-dir、--output 可覆盖默认目录；--profile /path/board.json 可使用尚未加入
机型目录的配置。输入和输出目录必须分开，避免覆盖原厂备份。
完整构建先在临时目录生成并校验，再替换该板自己的输出目录；失败保留上次
成功的产物。相同输出目录有构建锁，不同机型/模式可同时构建。

3. 新设备的原厂资料提取

先核对该设备 Linux 下实际的存储设备、IDB 偏移、U-Boot 分区和原厂 DTB 路径。
使用只读 SFTP 提取脚本，例如：

  python3 scripts/capture_rk3562_board.py \
    --board your-board --host DEVICE_IP --user root --insecure \
    --vendor-dtb /flash/VENDOR.dtb \
    --idb-device /dev/mmcblk0 --idb-offset 32768 --idb-size 524288 \
    --uboot-device /dev/disk/by-partlabel/uboot --uboot-size 4194304

设备路径与偏移必须换成该设备核实过的值。--insecure 表示不校验 SSH 主机指纹；
可不传该选项，使用已有 known_hosts。支持 --key；密码从 SSH_PASSWORD 环境
变量读取。脚本只通过 SFTP 以 rb 模式打开设备端文件，不执行远端写入命令。

提取结果：
  linux-vendor.dtb   原厂 Linux DTB
  idb-original.bin   从指定偏移起读取的 RKNS 槽
  uboot-original.img 原厂 U-Boot FIT/分区备份
  snapshot.json      来源主机、范围、机型和 SHA-256

已有快照不会覆盖，可以用 --output 保存其他批次，然后构建时用 --input-dir
选择。板子有不同硬件版本时应使用不同 board id，保留各自的 GPIO 与电池配置。

这里提取的是 DDR、BL31、BL32 等板上固件的载体；rkbin 的打包工具和 INI 仍由
共用、固定校验和的 rkbin 归档提供。完整板级构建强制使用该板 IDB/FIT 中的
固件覆盖归档默认值，不会因输入缺失回退到上一块板的固件。
可用 RKBIN 或 RKBIN_ARCHIVE 等底层参数更换打包工具集。

4. 新增板级配置

参考 ngp45h 目录新增一份配置，将 board.json 的 id 改为新机型。
共用 Python 构建程序不包含机型、IP、灯色或 GPIO 编号的分支。

board.json 的主要字段：
  schema_version：目前为 1。
  id：小写字母/数字/_/-，与机型目录一致。
  defconfig：本机型独立的 U-Boot defconfig，位于 configs/，必须选择本机控制树。
  control_dtb：name 指定早期 DTS 名称；power_init 明确电源初始化阶段；
               checks 检查最终 U-Boot/SPL 控制 DTB 的型号、时钟和接口策略。
               mmc_reference="vendor-boot" 从本机 IDB/FIT 提取早期参考；
               stage_checks 可分别约束 u-boot-spl.dtb 和 u-boot.dtb。
  inputs：linux_dtb、idb、uboot 三个输入文件名，相对于 --input-dir。
          linux_dtb 也可改为 linux_dts，接受反编译后的独立 DTS 文本。
  overlays：依次应用的 .dtso 文件，相对于 board.json 所在目录。
  checks：原厂 DTB 校验，可比较字符串、cells 或 GPIO 控制器/引脚/极性。
          不依赖原厂写的 EVB model 字符串判断板型。
  output_checks：对应用 overlay 后的 DTB 进行属性校验。
  dtb_mode：默认 embedded，板级配置和命令行可显式覆盖为 external。
  dtb_path：外部模式的启动分区路径，默认 /rk3562-charge.dtb。
  kconfig：可选字典，覆盖 defconfig，使用不带 CONFIG_ 前缀的符号名。
           JSON bool 为 y/n，字符串为 Kconfig 字符串，整数为数值。
  required_kconfig：该板必须实际启用的驱动，例如 PMIC、电量计、电源键驱动。
  source_overlay：可选源码目录，默认 source/，相对于 board.json 目录。

各机型必须在
  source/arch/arm/dts/rk3562-your-board.dts
放置板级 U-Boot DTS，在其独立 defconfig 中设置
  CONFIG_DEFAULT_DEVICE_TREE="rk3562-your-board"
并令 control_dtb.name 与之对应。构建器将本机充电 DTB 还原为带有符号标签的
rk3562-your-board-vendor.dtsi，再应用机型 DTS 和共用的 U-Boot 阶段策略。
不从其他机型或 EVB 复制电源、存储配置。源码补充仅复制到隔离构建树。
本机 DTS 在共用阶段策略之后包含 rk3562-your-board-mmc.dtsi；构建器会从
本机原厂 U-Boot 控制 DTB 生成它，并独立恢复 SPL 的阶段差异。

新设备继续采用以下流程：
  1. 保存本机原始 Linux DTB、eMMC IDB 和 U-Boot FIT，只读提取并记录 SHA-256。
  2. 独立配置本机供电、电池和充电灯；eMMC/TF1 的时钟、引脚、模式以原厂
     同一阶段的控制 DTB 为参考，不用 Linux 配置直接替代。
  3. 保持 TF1 优先，未插 TF1 时可从 eMMC 启动；不为此新增 TF2/Wi-Fi 初始化。
  4. 验证最终 IDB 确实包含本次编译的 SPL/DTB，DDR/BL31/BL32 与本机原始
     payload 一致。记录 TF1、无 TF 的 eMMC 冷启动及充电实测结果。
  5. 在 tools/ports/uboot/devices.json 注册本机精确 model，并上传输入包至
     CI 输入 Release。正式 Ports 包会自动汇总全部已登记机型。
仓库 AGENTS.md 同步约定了这些规则。

linux_dts 输入应是可直接交给 dtc 的独立或已预处理 DTS；包含 Linux 源码
#include 的 DTS 应先由对应 Linux 源码树预处理。overlay 若引用 &gpio3 等符号，
其基础 DTB 需要保留对应 __symbols__，或改用该板可解析的节点/句柄。

当前固件提取器支持 SHA-256 校验的外部数据 FIT、未压缩的任意数量 ATF 段、
加载地址为 0x08400000 的 OP-TEE，以及 RKNS IDB。遇到其他封装格式会停止，
需要扩展对应提取器后再使用；不会把无法解析的固件替换为默认版本。
BL31 的 ELF 是供 decode_bl31.py 使用的 PT_LOAD 打包容器，并非原始链接 ELF。
最终 FIT 中每段 BL31/BL32 的字节、地址、长度都与原厂提取结果核对。

5. 灯色与测试

GPIO、极性、普通充电灯/接近满电灯、SOC 阈值均由板级 DTS 配置。
NGP45H 当前为 GPIO3_C2 红灯、GPIO3_C1 蓝灯：低于 90% 红，90%..99% 蓝，
电量计报告 100% 时两灯熄灭。其他板可映射为红/绿等组合。
100% 使用电量计报告值，不单独读取硬件充电终止标志。
绑定说明见 doc/device-tree-bindings/power/rockchip-charge-leds.txt。

NGP45H 配置关闭自动退出充电及充电开屏，每 5 秒从 WFI 唤醒刷新电量/LED。
长按电源键、RTC 闹钟、控制台退出和特殊启动模式沿用现有框架。
NGP45H 的 v2/v3 诊断记录确认：所有充电依赖探测成功，mode:11 允许充电，
但 dc-level:0、sys-sts:130、online:0 导致继续启动 Linux。补齐 GPIO 时钟
初始化没有解决实机问题。随后做 Linux 开机插拔线对照：拔线后电流为
-378mA、PMIC f0 从 0xc0
变为 0x80，但 GPIO3_A7 仍高、AC online 仍为 1。原厂 dc_det_gpio 不能
作为本机可靠输入检测信号。v4-pmic 板级配置选择 PMIC 实时插入位，跳过该
GPIO，并显式关闭 OTG 输出、开启输入充电路径，首次在线查询最多等待 3 秒。
这些参数在板级 overlay 中，其他板默认保留原有检测及初始化行为。
v4-pmic 已通过用户实机验证：关机后插电不再自动进入 Linux，充电蓝灯亮。
验证镜像 SHA-256：
  d9590eb0b49c030fed2d4ceed5e8305f255a9397423ac36e0a562c493c2f8aab
镜像及刷写备份保存在 board-data/rk3562/ngp45h/flashes/20261018T110756Z/。
当前通过此项验证的版本记录于 board-data/rk3562/ngp45h/validated-charging.json。
用户后续反馈整体表现正常；各电量阈值及按键/拔线行为未单独留测量记录。

共用 RK817 驱动也支持原厂 dc_det_gpio 的可选解析，适用于已确认 GPIO
随插拔线变化的其他板。RK3562 GPIO 探测会从自己的 DTS clocks 属性开启
GPIO0..4 的 APB 总线及父级门控，采用高 16 位写掩码保留无关位。

没有串口时，新诊断版会在最终 Linux 启动参数中追加 uboot.charge=v4-pmic,...。
该记录在 extlinux 替换 bootargs 之后加入，包含 enabled、pmic、gauge、key、
soc、led-low、led-high、display、battery、lowpower、mode、online、voltage 等
阶段及返回值。entered 表示进入了充电循环；power-key、rtc-alarm、auto-soc、
auto-voltage、ctrlc 表示退出触发条件。display 为负数表示充电设备探测失败。
例如 mode:0 表示框架选择普通启动后跳过充电；online:0 表示 U-Boot 认为充电器
不在线。读取 /proc/cmdline 即可取回记录，不需要更改 Linux DTB 或用户系统。
v2-dc 额外记录 auto-exit、rk817、dc-valid、dc-base、dc-pin、dc-flags、
dc-level、dc-func、sys-sts、on-src；DC 记录只在首次在线查询时写入。
dc-level 是应用 DTS 极性后的逻辑值，dc-func 是 GPIO 驱动报告的功能枚举。
dc-base 是 U-Boot 动态编号，不能当作 Linux 的 GPIO bank 编号。
v3-gpio 增加 dc-reg（GPIO 控制器物理地址，有符号十进制）及 gpio-clk、
gpio-gate0/gpio-gate1（GPIO3/4 APB 时钟号、开启前/后的门控寄存器）。
v4-pmic 增加 detect-ms（首次检测等待）、sys-before（输入初始化前的 f0）、
usb-input（e6）、power-en（b4）、powerpath（e4）、usb-mv（USB ADC 电压）。
PMIC 检测配置下 dc-valid:0 是预期。on-src:64 是插电唤醒，源自 RK817
Datasheet Rev 2.1 的 ON_SOURCE[6]，不是实时插入状态，不能用来替代拔线检测。
这些记录用于定位，不会强制伪造“电源在线”或更改启动模式。
当前 NGP45H Linux DTB 可不含 dc_det_gpio；该板选择 PMIC 检测后，不再强制
校验此未使用的 GPIO 属性，红蓝灯的控制器、引脚及极性仍须与板级配置一致。

主机回归测试：
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3562_board.py -v
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk817_dc.py
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3562_gpio_clock.py
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3562_charge_leds.py
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3562_charge_trace.py

board-build.json 记录板号、DTB 模式、安装路径、输入 SHA-256、输出 SHA-256、
源码 commit/已跟踪差异摘要、构建脚本摘要和快照摘要。u-boot.config 保存实际
生效配置，requested_defconfig 保存请求配置，build.log 保存完整编译日志。
每次完整构建都检查固件 SHA、DDR 与 BL31/BL32 逐字节一致性；内嵌模式还从
最终 FIT 取出 kern-fdt，核对与生成的充电 DTB 完全一致。

6. GitHub Actions 与 Release

.github/workflows/rk3562.yml 自动从 rk3562-profiles/*/board.json 生成设备矩阵，
目前包含 ngp45h 和 pocket-ais。每台设备独立下载自己的原厂输入、编译、验证并打包。
默认 embedded 模式；手动运行时也可选 external。使用 Ubuntu 22.04 和
Linaro GCC 6.3.1-2017.05 aarch64-linux-gnu，不依赖开发机的 /opt 路径。

工具链下载先尝试原 releases.linaro.org 地址；该旧地址目前会跳转到联系页面，
因此同时提供 Arm 官方 Legacy Linaro 镜像作为备用。两者均须符合固定 SHA-256：
  26b45fdfb1656f94fa2b5117494f5d338b619c1f453ee3f3e1d84d79e90f2403
工具链归档通过 Actions cache 缓存，每次使用前校验并重新解压。
官方版本列表：
  https://developer.arm.com/Downloads/-/Legacy%20Linaro%20GNU%20Toolchains

首次配置原厂输入（每台设备执行一次）：

  python3 scripts/ci_rk3562.py pack-inputs --board ngp45h \
    --input-dir /path/to/verified/ngp45h

生成 build-rk3562-inputs/ngp45h-inputs.tar.xz 及同名 .sha256 文件。
输入目录需要含 board.json 指定的 DTB/DTS、IDB、U-Boot；已有 snapshot.json
会先验证。打包只包含这三个输入和重新生成的 board_id/SHA-256 清单，不包含
设备 IP、诊断日志或刷写备份。DTS 输入应自包含，不依赖目录外的 include 文件。

将两个文件上传到本仓库独立的输入 Release，标签为 rk3562-inputs-v1。例如：

  gh release create rk3562-inputs-v1 --target rk3562 \
    --title "RK3562 vendor build inputs v1" --notes "Board-specific build inputs" \
    --prerelease --latest=false \
    build-rk3562-inputs/ngp45h-inputs.tar.xz \
    build-rk3562-inputs/ngp45h-inputs.tar.xz.sha256

输入 Release 的标签不会触发固件发布。未来新增设备时，将该设备的输入包和
.sha256 文件追加到该输入 Release，即可由自动发现的设备矩阵构建。
输入包先检查归档 SHA-256，再检查板号、文件清单和每个输入的 SHA-256。
不接受归档中的链接或其他文件。输入下载失败或板级验证失败会中止该设备构建。

可选 Repository variables（Settings -> Secrets and variables -> Actions）：
  RK3562_INPUTS_TAG         输入 Release 标签，默认 rk3562-inputs-v1。
  RK3562_INPUTS_REPOSITORY  输入 Release 所在 owner/repo，默认当前仓库。
  RK3562_TOOLCHAIN_URL     同一份工具链 tar.xz 的自定义下载地址，仍校验固定 SHA。
默认使用自动提供的 GITHUB_TOKEN，无需另配 PAT。同仓库私有 Release 也支持；
其他仓库需允许当前 token 读取，公开输入仓库可直接读取。

触发方式：
  推送 rk3562 分支或向该分支提交 PR：编译所有已注册设备，保存 30 天 Artifact。
  推送 rk3562-v* 标签（如 rk3562-v1.0.0）：全部设备构建成功后创建 GitHub Release。
  Actions -> RK3562 U-Boot -> Run workflow：选择运行分支；boards 填 all，或
    ngp45h 等逗号分隔板号；可选 DTB 模式。publish 默认关闭，勾选后会发布，
    分支运行生成唯一标签 rk3562-build-<run_id>，标签运行使用当前标签。
    正式发布会固定 boards=all、dtb_mode=embedded；单机型/external 选项仅用于
    不发布的调试构建。任一机型失败或缺失，均不发布统一 Ports 包。

新 workflow 首次手动运行通常需要它先出现在默认分支；当前 rk3562 分支的
push/tag 触发不受此限制。发布作业单独获取 contents:write；构建作业只读。
仓库或组织策略需允许 Actions 发布 Release。失败构建保存控制台日志 7 天。

面向用户的统一安装包：
  rk3562-update-uboot-ports-<commit前12位>.zip
  同名 .sha256

ZIP 根目录为 Update U-Boot.sh 和 uboot/；uboot 内含全部机型的组合镜像、
精确 model 映射、更新程序、提示图片及构建/校验记录。下载后直接解压到
/roms/ports/，刷新 Ports 列表后运行 Update U-Boot 即可；无需用户选板号。
覆盖安装包时保留已有 uboot/backups/ 和 uboot/logs/。包内不包含这些运行目录。
安装包只会更新 /flash 所在磁盘的启动区，不会自动重启或迁移系统到 eMMC。

embedded 构建完成后，ports 作业从各机型 Artifact 汇总镜像。它校验机型集合、
同一提交/干净源码状态、镜像和校验报告 SHA-256、实际打包 SPL 和启动顺序，
生成 ZIP 后保存为 rk3562-ports Artifact；release 作业再将 ZIP 和逐板构建包
一起发布。普通分支构建也会生成 ZIP Artifact，便于先测试再打发布标签。

每台设备发布两个文件：
  rk3562-<board>-<embedded|external>-<commit前12位>.tar.xz
  同名 .sha256

解压目录内的 SHA256SUMS 可用 sha256sum -c SHA256SUMS 检查完整性。
包含 uboot.img、idblock.img、rk3562-uboot-16m.bin、DTB、实际配置、编译日志、
board-build.json 等完整产物。package 步骤会复核板号和构建时记录的产物哈希。
uboot.img 是 U-Boot 分区镜像；rk3562-uboot-16m.bin 是从磁盘 sector 64 起
写入的组合镜像，文件长度为 16 MiB - 32 KiB。具体写入位置应沿用设备已验证
的流程。不同板号或硬件版本的输入和产物不可混用。

CI 辅助脚本回归测试：
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_ci_rk3562.py -v

7. Pocket AIS 板级适配

profile：board/rockchip/rk3562-profiles/pocket-ais/board.json。
基于 Pocket AIS 自己的 rk3562-pocket-ais.dtb、RKNS IDB 和 U-Boot FIT 快照；
其中 BL31 v1.21、BL32 v1.07 与 NGP45H 的固件版本不同，不共用输入包。
DDR、BL31、BL32 在重建后逐字节检查，充电 DTB 默认内嵌。

硬件配置从该机 DTB 保留：RK817、3675 mAh design_capacity、4042 mAh
design_qmax、107 mOhm bat_res 以及原 OCV 表。红灯 GPIO3_A1，绿灯 GPIO3_A0，
均为 GPIO_ACTIVE_HIGH。overlay 复用原 charge_led_gpio 的引脚配置。
板级输入检查严格核对机型、PMIC、电池数据及两根充电 LED 接线；不能使用
NGP45H 的 GPIO3_C2/C1 输入冒充此机型。

没有 dc_det_gpio，使用 RK817 实时 USB 插入状态，并初始化输入充电路径。
关机插电时保持关屏，只控制充电指示灯：90% 以下红灯、90%..99% 绿灯、
100% 熄灯，每 5 秒从 WFI 唤醒刷新。按键退出充电沿用共用框架。
本次适配只增加充电 LED 节点和充电策略，不改动原 LCD、风扇、Wi-Fi、
音频及游戏按键配置，也不改写 Linux 使用的 DTB。

Ports 更新器按设备树 model 精确匹配 Pocket AIS，选择 pocket-ais-uboot.bin。
开发机可同时打包两台设备：
  python3 scripts/package_rk3562_ports.py \
    --build-dir /path/to/ngp45h/build --build-dir /path/to/pocket-ais/build

构建及固件/DTB 静态校验已完成。关机插电、电源键退出、各电量灯色和满电
熄灯仍需在此机实测；不能将 NGP45H 的实机验证当作 Pocket AIS 的验证记录。

8. 独立 defconfig、控制 DTB 和电源初始化阶段

每个机型由独立 board.json 绑定以下内容：
  ngp45h：configs/rk3562_ngp45h_defconfig、rk3562-ngp45h.dts、NGP45H 输入包。
  pocket-ais：configs/rk3562_pocket_ais_defconfig、rk3562-pocket-ais.dts、Pocket AIS 输入包。
两个机型分别构建 SPL 控制 DTB、U-Boot 控制 fdt、内嵌充电 kern-fdt 和镜像。
启动存储配置从各机型自己的原始 IDB/FIT 提取，不用 Linux DTB 代替早期控制
DTB。两台原始 SPL DTB 中，eMMC 都为 8 位、200 MHz 上限、HS400/增强选通；
原始 U-Boot DTB 中，NGP45H 不声明 HS400，Pocket AIS 保留 HS400/增强选通。
两阶段都保留 fixed-emmc-driver-type = <1>。TF1 上限均为 150 MHz，引脚复用、
上下拉和驱动强度均与对应阶段的原始控制 DTB 对比。频率上限不等于实际协商
频率。串口仍选择与 CONFIG_DEBUG_UART_BASE 对应的本机 UART0。

硬件数据以设备提取的 DTB 为基准，可对照 distribution 中对应 DTS；构建器
恢复 __symbols__ 为 DTS 标签，保留原始属性和供电引用，不依赖 EVB 的 LDO 表。
rk3562-profile-u-boot.dtsi 定义 U-Boot 所需阶段标记、SCMI 安全时钟路径等。
其后生成的 <机型>-mmc.dtsi 用原厂 U-Boot 控制 DTB 覆盖 eMMC/TF1 配置，
重新映射 phandle，并保留原厂引脚参数。SPL_DTB_OVERLAY 在 SPL 打包前恢复
本机原厂 SPL 与 U-Boot 的阶段差异。目前仅允许原厂 HS400 属性的增补，其他
阶段差异必须显式实现后才允许构建。

当前两台设备的 control_dtb.power_init 均为 board-dtb：
  SPL：CONFIG_SPL_DM_PMIC/CONFIG_SPL_DM_REGULATOR 关闭；不重新设置 LDO。
  早期 U-Boot：裁剪后的控制树中不保留 PMIC/regulator，不提前设置板级电压；
    原厂两阶段控制 DTB 同样没有 PMIC/regulator 或 MMC supply 引用；早期
    eMMC/TF1 沿用前级供电，不新增 SD UHS 电压切换流程。
  board_init：init_kernel_dtb() 切换至本机 kern-fdt，然后初始化本机 regulator；
    此后的电源、充电、LED 等均按本机数据执行。SPL 不会把 EVB LDO 值提前写死。
新增板若需要更早的电压初始化，需为它实现对应阶段策略，不能只改 power_init
字段绕过检查。

构建新增验证：
  检查实际 .config 选择本机 DEFAULT_DEVICE_TREE；SPL/U-Boot DTB 必须有本机 model。
  检查 FIT 内 fdt 与 u-boot.dtb 完全相等，kern-fdt 与本机充电 DTB 完全相等。
  对照原始 DTB，检查完整控制树及充电 DTB 的全部 regulator/IO domain 节点。
    按目标节点路径解析 supply、GPIO、pinctrl、PWM 引用，避免 phandle 重编号误判。
    电压范围、开关标志、GPIO、供电关系有任何变化即中止构建。
  检查 board-dtb 策略下裁剪后的早期树没有 PMIC/regulator。
  检查早期只保留 eMMC 和 TF1；别名为 mmc0=eMMC、mmc1=TF1，SPL 的读取
    顺序为 TF1、eMMC。TF1 无卡或无法加载有效镜像时，沿用 SPL 的回退机制。
  对照原厂同一阶段 DTB，检查全部 MMC 属性、引用的时钟/复位参数和引脚配置。
  打包必须使用 --spl-new；单独 --spl 不会传递到 FIT 打包器，后者会使用
    rkbin 自带的通用 SPL。构建从最终 IDB 再次提取 SPL，与本次编译的 SPL
    逐字节比较（仅允许尾部对齐补零），并确认其内包含本机 SPL 控制 DTB。

产物增加 u-boot-spl.dtb、u-boot-control-full.dtb（裁剪前的完整控制树）和
board-power.json（电源配置摘要与阶段策略）。上述文件同样纳入产物 SHA-256。
另增加 vendor-spl-control.dtb、vendor-uboot-control.dtb、packed-spl.bin、
board-mmc.json，记录原始参考、实际打包的 SPL 和各阶段的 MMC 校验结果。
后续充电 DTB 保留本机 Linux 的 TF2/Wi-Fi 与供电配置，不为它们增加早期初始化。
这些是编译和静态校验结果；拔 TF 后的 eMMC 冷启动、插 TF 的优先启动仍需实测。

这两份 RK3562 输入没有独立 io-voltage-domain 节点，CONFIG_IO_DOMAIN 保持
关闭；当前没有增加 IO 电压选择寄存器写入，也没有套用 RK3568 的 IO domain
驱动。这里验证的是板级配置及执行阶段，实际引脚电压仍需在硬件上核实。

GitHub Actions 自动发现 board.json，在各自矩阵任务内选择其 defconfig、DTS
和输入包。任一机型验证失败会使该次固件发布停止。

专项测试：
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3562_control_dtb.py -v
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_package_rk3562_ports.py -v

统一 Ports 包的本地构建示例：
  python3 scripts/package_rk3562_ports.py \
    --build-dir build-rk3562-mmc-aligned/ngp45h \
    --build-dir build-rk3562-mmc-aligned/pocket-ais \
    --require-all --output build-rk3562-ports-release --archive-dir dist
CI 可用 --build-root 自动发现下载目录中的逐机型 Artifact，使用
--expected-boards/--source-commit/--require-clean 严格检查本次矩阵和源码来源。

2026-10-19 实机反馈：用户在 NGP45H 和 Pocket AIS 更新新的镜像后反馈测试
没有问题。后续新增设备仍需分别记录启动介质和充电场景的实测范围。
