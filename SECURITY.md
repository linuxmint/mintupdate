# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 7.0.7   | :x:                |

## Reporting a Vulnerability

Critical security flaw found on mintUpdate!

After canceling the update — aborting from the password prompt window by pressing [Esc] — the selected Flatpak packages were still updated!
But the expected behavior would be that nothing gets updated, right?!

## Informatio of system
System:
  Kernel: 6.8.0-63-generic arch: x86_64 bits: 64 compiler: gcc v: 13.3.0 clocksource: tsc
  Desktop: Cinnamon v: 6.4.8 tk: GTK v: 3.24.41 wm: Muffin v: 6.4.1 vt: 7 dm: LightDM v: 1.30.0
    Distro: Linux Mint 22.1 Xia base: Ubuntu 24.04 noble
Machine:
  Type: Laptop System: Dell product: Inspiron 3480 v: N/A serial: <superuser required> Chassis:
    type: 10 serial: <superuser required>
  Mobo: Dell model: 07MC29 v: A00 serial: <superuser required> part-nu: 08C7
    uuid: <superuser required> UEFI: Dell v: 1.30.0 date: 04/10/2024
Battery:
  ID-1: BAT0 charge: 14.5 Wh (64.2%) condition: 22.6/42.0 Wh (53.9%) volts: 11.6 min: 11.4
    model: BYD DELL 1VX1H0A type: Li-poly serial: <filter> status: discharging
CPU:
  Info: dual core model: Intel Pentium 5405U bits: 64 type: MT MCP smt: enabled
    arch: Comet/Whiskey Lake note: check rev: C cache: L1: 128 KiB L2: 512 KiB L3: 2 MiB
  Speed (MHz): avg: 2300 min/max: 400/2300 cores: 1: 2300 2: 2300 3: 2300 4: 2300 bogomips: 18399
  Flags: ht lm nx pae sse sse2 sse3 sse4_1 sse4_2 ssse3 vmx
Graphics:
  Device-1: Intel Whiskey Lake-U GT1 [UHD Graphics 610] vendor: Dell driver: i915 v: kernel
    arch: Gen-9.5 ports: active: eDP-1 empty: HDMI-A-1 bus-ID: 00:02.0 chip-ID: 8086:3ea1
    class-ID: 0300
  Device-2: Microdia Integrated_Webcam_HD driver: uvcvideo type: USB rev: 2.0 speed: 480 Mb/s
    lanes: 1 bus-ID: 1-6:4 chip-ID: 0c45:671e class-ID: 0e02
  Display: x11 server: X.Org v: 21.1.11 with: Xwayland v: 23.2.6 driver: X: loaded: modesetting
    unloaded: fbdev,vesa dri: iris gpu: i915 display-ID: :0 screens: 1
  Screen-1: 0 s-res: 1366x768 s-dpi: 96 s-size: 361x203mm (14.21x7.99") s-diag: 414mm (16.31")
  Monitor-1: eDP-1 model: AU Optronics 0xb68d res: 1366x768 hz: 60 dpi: 112
    size: 309x173mm (12.17x6.81") diag: 354mm (13.9") modes: 1366x768
  API: EGL v: 1.5 hw: drv: intel iris platforms: device: 0 drv: iris device: 1 drv: swrast gbm:
    drv: iris surfaceless: drv: iris x11: drv: iris inactive: wayland
  API: OpenGL v: 4.6 compat-v: 4.5 vendor: intel mesa v: 24.2.8-1ubuntu1~24.04.1 glx-v: 1.4
    direct-render: yes renderer: Mesa Intel UHD Graphics 610 (WHL GT1) device-ID: 8086:3ea1
Audio:
  Device-1: Intel Cannon Point-LP High Definition Audio vendor: Dell driver: snd_hda_intel
    v: kernel bus-ID: 00:1f.3 chip-ID: 8086:9dc8 class-ID: 0403
  API: ALSA v: k6.8.0-63-generic status: kernel-api
  Server-1: PipeWire v: 1.0.5 status: active with: 1: pipewire-pulse status: active
    2: wireplumber status: active 3: pipewire-alsa type: plugin
Network:
  Device-1: Intel Cannon Point-LP CNVi [Wireless-AC] driver: iwlwifi v: kernel bus-ID: 00:14.3
    chip-ID: 8086:9df0 class-ID: 0280
  IF: wlo1 state: up mac: <filter>
  Device-2: Realtek RTL810xE PCI Express Fast Ethernet vendor: Dell driver: r8169 v: kernel pcie:
    speed: 2.5 GT/s lanes: 1 port: 3000 bus-ID: 01:00.0 chip-ID: 10ec:8136 class-ID: 0200
  IF: enp1s0 state: down mac: <filter>
  IF-ID-1: docker0 state: down mac: <filter>
Bluetooth:
  Device-1: Intel Bluetooth 9460/9560 Jefferson Peak (JfP) driver: btusb v: 0.8 type: USB rev: 2.0
    speed: 12 Mb/s lanes: 1 bus-ID: 1-10:5 chip-ID: 8087:0aaa class-ID: e001
  Report: hciconfig ID: hci0 rfk-id: 0 state: up address: <filter> bt-v: 5.1 lmp-v: 10 sub-v: 100
    hci-v: 10 rev: 100 class-ID: 7c010c
Drives:
  Local Storage: total: 342.81 GiB used: 213.98 GiB (62.4%)
  ID-1: /dev/nvme0n1 model: SSD NTC 128GB NVMe size: 119.24 GiB speed: 31.6 Gb/s lanes: 4
    tech: SSD serial: <filter> fw-rev: V0530B3 temp: 32.9 C scheme: GPT
  ID-2: /dev/sda model: SATA SSD size: 223.57 GiB speed: 6.0 Gb/s tech: SSD serial: <filter>
    fw-rev: 61.3 scheme: GPT
Partition:
  ID-1: / size: 103.14 GiB used: 25.07 GiB (24.3%) fs: btrfs dev: /dev/nvme0n1p3
  ID-2: /boot/efi size: 100.4 MiB used: 34.2 MiB (34.1%) fs: vfat dev: /dev/nvme0n1p1
  ID-3: /home size: 223.57 GiB used: 188.86 GiB (84.5%) fs: btrfs dev: /dev/sda6
Swap:
  ID-1: swap-1 type: partition size: 16 GiB used: 20.5 MiB (0.1%) priority: -2 dev: /dev/nvme0n1p2
USB:
  Hub-1: 1-0:1 info: hi-speed hub with single TT ports: 12 rev: 2.0 speed: 480 Mb/s lanes: 1
    chip-ID: 1d6b:0002 class-ID: 0900
  Device-1: 1-6:4 info: Microdia Integrated_Webcam_HD type: video driver: uvcvideo interfaces: 2
    rev: 2.0 speed: 480 Mb/s lanes: 1 power: 500mA chip-ID: 0c45:671e class-ID: 0e02
  Device-2: 1-10:5 info: Intel Bluetooth 9460/9560 Jefferson Peak (JfP) type: bluetooth
    driver: btusb interfaces: 2 rev: 2.0 speed: 12 Mb/s lanes: 1 power: 100mA chip-ID: 8087:0aaa
    class-ID: e001
  Hub-2: 2-0:1 info: super-speed hub ports: 4 rev: 3.1 speed: 10 Gb/s lanes: 1 chip-ID: 1d6b:0003
    class-ID: 0900
Sensors:
  System Temperatures: cpu: 43.0 C pch: 41.0 C mobo: 35.0 C
  Fan Speeds (rpm): cpu: 0
Repos:
  Packages: 2346 pm: dpkg pkgs: 2338 pm: flatpak pkgs: 8
  No active apt repos in: /etc/apt/sources.list
  Active apt repos in: /etc/apt/sources.list.d/cloudflare-client.list
    1: deb [arch=amd64 signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https: //pkg.cloudflareclient.com/ noble main
  Active apt repos in: /etc/apt/sources.list.d/google-chrome.list
    1: deb [arch=amd64] https: //dl.google.com/linux/chrome/deb/ stable main
  Active apt repos in: /etc/apt/sources.list.d/official-package-repositories.list
    1: deb https: //mirror.ufscar.br/mint-archive xia main upstream import backport
    2: deb http: //mirror.ufscar.br/ubuntu noble main restricted universe multiverse
    3: deb http: //mirror.ufscar.br/ubuntu noble-updates main restricted universe multiverse
    4: deb http: //mirror.ufscar.br/ubuntu noble-backports main restricted universe multiverse
    5: deb http: //security.ubuntu.com/ubuntu/ noble-security main restricted universe multiverse
  Active apt repos in: /etc/apt/sources.list.d/vivaldi.list
    1: deb [arch=amd64] https: //repo.vivaldi.com/stable/deb/ stable main
  Active apt repos in: /etc/apt/sources.list.d/warpdotdev.list
    1: deb [arch=amd64 signed-by=/etc/apt/trusted.gpg.d/warpdotdev.gpg] https: //releases.warp.dev/linux/deb stable main
  Active apt repos in: /etc/apt/sources.list.d/wavebox-stable.list
    1: deb [arch=amd64] https: //download.wavebox.app/stable/linux/deb/ amd64/
  Active apt repos in: /etc/apt/sources.list.d/vscode.sources
    1: deb [arch=amd64,arm64,armhf] https: //packages.microsoft.com/repos/code stable main
Info:
  Memory: total: 16 GiB available: 15.47 GiB used: 5.47 GiB (35.4%)
  Processes: 344 Power: uptime: 1d 8h 8m states: freeze,mem,disk suspend: deep wakeups: 2
    hibernate: platform Init: systemd v: 255 target: graphical (5) default: graphical
  Compilers: gcc: 13.3.0 Client: Cinnamon v: 6.4.8 inxi: 3.3.34
