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

### Attaching the device to WSL

![](2026-05-01-05-09-36.png)

Bind the device as Administrator
![](2026-05-01-05-11-13.png)

Attach the device to WSL
![](2026-05-01-05-11-35.png)
You should hear a Windows plug-in sound

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
