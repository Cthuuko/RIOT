# Development on WSL

To install WSL refer to this [guide](https://doc.riot-os.org/getting-started/install-wsl/) by RIOT-OS.

Attaching a device to WSL refer [here](https://doc.riot-os.org/getting-started/install-wsl/#attach-a-usb-device-to-wsl)

## Set-Up RIOT-OS
Refer to this [guide](https://doc.riot-os.org/getting-started/installing/)

The ubuntu packages required
```
sudo apt install make gcc-multilib python3-serial python3-psutil wget unzip git openocd gdb-multiarch esptool podman-docker clangd clang
```

## Flashing the nRF52840 Dongle

More information can be found under /boards/nrf52840dongle/doc.md

### Prerequisite
[nrfutil](https://www.nordicsemi.com/Products/Development-tools/nRF-Util) needs to be installed. Refer to this [guide](https://docs.nordicsemi.com/bundle/nrfutil/page/guides/installing.html)

![](2026-05-01-05-47-11.png)

`nrfutil install nrf5sdk-tools` needs to be executed

### Attaching the device to WSL

![](2026-05-01-05-09-36.png)

Bind the device as Administrator
![](2026-05-01-05-11-13.png)

Attach the device to WSL
![](2026-05-01-05-11-35.png)
You should hear a Windows plug-in sound

ATTENTION:
To flash the dongle, you have to press the RESET button on the dongle.
Refer [here](https://docs.nordicsemi.com/bundle/ug_nrf52840_dongle/page/UG/nrf52840_Dongle/programming.html)
![](2026-05-01-06-31-28.png)

This is the VID:PID once it's in DFU Bootloader Mode
![](2026-05-01-06-18-48.png)

This is the VID:PID once it's flashed 
![](2026-05-01-06-20-22.png)

Each time you have to reattach the device in Powershell, if WSL is in use.

### Flash hello-world
```
cd ~/RIOT-Base/examples/basic/hello-world
make

```

# FAQ


## Cannot run hello-world in WSL Ubuntu
Problem might arise due to wrong file endings during the repository check-out in WSL

e.g. when using `make`
```
/generate_pp_successor_header.sh: not found
or
/genconfigheader.sh: not found
```

Solution:
This command retains the line endings from the repository
```
git config --global core.autocrlf input
```

