ROCKNIX Ports U-Boot updater
==========================

从 GitHub Release 下载 rk3562-update-uboot-ports-<版本>.zip，将压缩包里的内容
直接解压到 /roms/ports/，不要再套一层目录，然后刷新 Ports 游戏列表。
包内包含本次发布支持的全部机型；运行 Update U-Boot 后自动选择本机镜像。
重新解压覆盖同名程序/镜像即可更新安装包，保留原有 backups 和 logs 目录。

安装位置：
  /roms/ports/Update U-Boot.sh
  /roms/ports/uboot/update_uboot.py
  /roms/ports/uboot/devices.json
  /roms/ports/uboot/<board>-uboot.bin
  /roms/ports/uboot/screens/{updating,success,failure}.png

在 Ports 中运行 Update U-Boot，会自动读取运行中设备树的 model/compatible，
按 devices.json 精确匹配机型及二进制，更新 /flash 所在启动卡的 U-Boot。
当前支持 Xifan NGP45H（ngp45h-uboot.bin）和 Pocket AIS
（pocket-ais-uboot.bin），均内嵌各自的充电 DTB。
不固定 mmcblk 编号。遇到未知/重复机型、兼容性不符、镜像校验不符、启动盘
无法识别或写入区域与分区重叠，均会中止。

流程：mpv 显示更新画面，检查机型/镜像/磁盘边界，备份磁盘前 16 MiB，
将组合镜像用 dd 从 sector 64 写入当前启动卡，fsync，直接读回并核对 SHA-256。
更新画面至少 3 秒；结果画面另显示至少 3 秒，然后关闭本次启动的 mpv。
不自动重启；更新成功后下次启动生效。

备份：uboot/backups/<UTC时间>/boot-front-before.bin，包含原分区表及 U-Boot。
记录：同目录 before.json、dd.log、verify-dd.log，以及 uboot/logs/*-result.json。
失败时保留备份与日志；不会自动刷回或重启。

只检查，不刷写、不显示界面：
  bash '/roms/ports/Update U-Boot.sh' --check
仅预览界面，不刷写：
  bash '/roms/ports/Update U-Boot.sh' --preview success
  bash '/roms/ports/Update U-Boot.sh' --preview failure

后续支持其他机型：
  在 devices.json 的 devices 数组追加一项，id 与文件名 <id>-uboot.bin 对应；
  models 填设备树 /model 的精确字符串，compatible 填对应 SoC；
  配置镜像 sha256、size、offset、source_commit，并放入该机型已验证的镜像。
  当前更新器只接受相同的 RK3562 sector-64 组合镜像布局；其他布局需单独适配。
  同一 model 不能对应多个设备条目，硬件版本需通过设备树区分后再配置。

在开发机制作安装目录（需要 Pillow 和 Noto CJK 字体）：
  python3 scripts/package_rk3562_ports.py --build-dir /path/to/verified/build
  可重复 --build-dir，将多个机型的构建一起放入同一安装目录。
  产物在 build-rk3562-ports/，可将内容复制到设备 /roms/ports/。
  --require-all 检查是否包含全部机型；--archive-dir dist 同时生成可直接解压
  安装的 ZIP 和同名 .sha256。正式 Release 总是打包全部机型的 embedded 镜像。
