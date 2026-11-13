RK3566 Powkiddy X35H：板级 U-Boot 与关机充电灯
==========================================

板号 powkiddy-x35h，运行中 DT model 精确匹配 "Powkiddy x35h"。
独立配置位于 board/rockchip/rk3566-profiles/powkiddy-x35h/，defconfig 为
configs/rk3566_powkiddy_x35h_defconfig，控制 DTS 为 rk3566-powkiddy-x35h。
RK3566 使用本仓库 RK3568 的 SoC 驱动，loader 选择 RK3566MINIALL.ini；
RK3568TRUST.ini 仅提供打包格式，DDR/BL31/BL32 全部由本机原始输入替换。

构建：
  ./build_uboot_rk3566_board.sh --list-boards
  ./build_uboot_rk3566_board.sh --board powkiddy-x35h --dtb-only
  ./build_uboot_rk3566_board.sh --board powkiddy-x35h

默认输入 board-data/rk3566/powkiddy-x35h/，输出
build-rk3566-boards/powkiddy-x35h/embedded/。支持 --input-dir、--output。
输入和完整输出的校验、隔离构建、固件保存规则与 README.rk3562-charge.txt 相同。
共用 Python/后端脚本保留 rk3562 文件名，--soc/SOC 显式选择平台，不混用输入。

原始输入来源
------------

2026-11-13 从调试设备只读提取：
  linux-vendor.dtb：eMMC Android boot 分区的 second/resource 镜像中的硬件 DTB。
  idb-original.bin：eMMC offset 32768，长度 524288。
  uboot-original.img：原厂 eMMC uboot 分区，offset 8 MiB，长度 4 MiB。
保留前 16 MiB、运行中 DTB、TF 原启动区及提取位置作为本地诊断备份，不提交 Git。
云端输入归档只包含上述三个文件和不含主机地址的 snapshot.json。
输入 Release 标签为 rk3566-inputs-v1，不作为用户安装包。

原厂 IDB/SPL 和 U-Boot FIT 的控制 DTB 相同，SHA-256：
  9679fe2a45776f35494f95ccbf57831f94c54cc90b3766a2f7371e169ac928fb
profile 固定这两个阶段的指纹，避免把 TF 上 Quartz64 配置的 U-Boot 当作原厂参考。

早期启动和电源
--------------

eMMC /sdhci@fe310000：8-bit、200 MHz、HS200 1.8 V。
TF1 /dwmmc@fe2b0000：150 MHz，采用本机原厂各阶段时钟、复位、引脚和驱动强度。
先 TF1，后 eMMC；不增加 TF2 /dwmmc@fe000000 或 Wi-Fi /dwmmc@fe2c0000
的早期初始化。原始 SPL 虽含 Wi-Fi 控制器，本机早期树仅保留启动必需的两路。
Linux/充电完整 DTB 的四路存储及其供电关联不因此改变。

早期不初始化 PMIC/regulator；进入本机 embedded kern-fdt 后初始化供电。
供电域、LDO、电压、调压器关联和 IO domain 取自本机原厂硬件 DTB，保持完整。
本机启用 IO_DOMAIN 驱动，校验报告 board-power.json 检查 40 个电源相关节点。
GPIO APB 时钟按本机 GPIO 节点开启，使用 RK3568 CRU 的 GPIO0..4 门控及父门控，
高 16 位掩码写保留无关位，不假设上一阶段已开启 GPIO4。

最终 IDB 使用 --spl-new，并重新提取 SPL，逐字节检查编译结果和控制 DTB。
DDR、BL31/BL32 的内容、长度和加载地址必须与本机原厂一致。
ATF 节点编号可能因 ELF 段排序改变，比较时按类型/地址/长度/哈希核对完整集合。
不启用 FIT 压缩；关闭无关屏幕驱动后，保持两份 2 MiB FIT 的 4 MiB 布局。
组合镜像 rk3566-uboot-16m.bin 长度 16 MiB - 32 KiB，从磁盘 sector 64 起写。

电池参数和灯色
--------------

参考 distribution 工程：
  projects/ROCKNIX/devices/RK3566/linux/dts/rockchip/rk3566-powkiddy-x35h.dts
  projects/ROCKNIX/devices/RK356X/linux/dts/rockchip/rk3566-powkiddy-x35h.dts
其中 RK356X 文件已有适用于 vendor RK817 驱动的 battery 参数。
原厂镜像电池配置为 3128 mAh / 3440 mAh / 112 mOhm；本 profile 按 distribution
的 X35H 校准改为 2718 mAh / 2990 mAh / 94 mOhm，OCV 表以该机型为准。
保留本机原有充电器的电流/电压限制，不复制 RK3562 的电池和充电配置。

原厂 charger GPIO 与 distribution 的 LED 节点对应：
  GPIO4_B4（12）：红灯，SOC 0..89%。
  GPIO4_B5（13）：绿灯，SOC 90..99%。
  GPIO4_B6（14）：原厂黄色充电通道，当前保持关闭。
SOC 报告 100% 时关闭两盏软件指示灯。颜色名称来自设备树，实际颜色待实机确认。
通过 RK817 实时 PMIC 在线位检测充电器，开启输入充电路径，首次等待最多 3 秒。
充电时不开屏，不按电量自动退出；每 5 秒刷新 SOC 和 LED，沿用电源键退出机制。
长按电源键、拔线和实际功耗仍须实测，构建通过不代表这些硬件场景已经验证。
/proc/cmdline 的 uboot.charge 诊断仍可用于取回探测及退出结果。

测试和发布
----------

运行 README.rk3562-charge.txt 的回归项，另加：
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3566_board.py -v
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_rk3566_gpio_clock.py

本地已完成完整镜像构建及板级校验，尚未刷写调试设备。
TF1 启动：待实测；不插 TF 的 eMMC 冷启动：待实测；关机充电灯：待实测。
调试时只安装 Ports 文件和运行 --check，不自动刷写、重启设备。

同一固件 Release 发布两个附件，均以日期和版本号命名：
  rk3562-update-uboot-ports-YYYY-MM-DD-vX.Y.Z.zip：NGP45H、Pocket AIS。
  rk3566-update-uboot-ports-YYYY-MM-DD-vX.Y.Z.zip：Powkiddy X35H。
每个平台的 ZIP 汇总该平台全部 profile，两个包来自同一源码提交。
仅这两个 Ports ZIP 附在 Release，逐板包、单独校验文件在 Actions Artifacts。

工作流仍为 .github/workflows/rk3562.yml，显示名 RK356x U-Boot。
推送 rk3562 分支编译三个机型；推送 rk356x-vX.Y.Z-YYYY-MM-DD 标签发布。
先推送分支提交，再推送标签。默认统一版本读取 RK356X_VERSION。
手动 publish 强制编译全部平台和机型；任一失败则不发布正式 Release。

新增 RK3566 设备需要独立 profile/defconfig/DTS/原厂输入/精确 model 注册，
并补充 RK3566 输入 Release。矩阵自动发现新板号，不能复用 X35H 的电源和电池。
