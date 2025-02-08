# Docker Images for Side Projects

This repository contains Dockerfiles and GitHub Actions workflows for building Docker images for various side projects. The projects include:

- **Remote Desktop Environments**: Docker images providing remote desktop access with different desktop environments:
  - `rdesktop-debian-xfce`: Debian with XFCE desktop.
  - `rdesktop-ubuntu-kde`: Ubuntu with KDE desktop.
- **Java Development Support**: Integration of Microsoft Build of OpenJDK version 21 for Java applications.
- **Apache Guacamole**: Docker images for Guacamole client and server version 1.5.5, providing a clientless remote desktop gateway.
- **Macless Haystack**: An all-in-one solution to set up a custom FindMy network without needing a Mac or installing additional plugins:
  - `macless-haystack-anisette`: Anisette server for Apple authentication.
  - `macless-haystack-backend`: Backend services for Macless Haystack.
  - `macless-haystack-frontend`: Frontend interface for Macless Haystack.
- **Tailscale VPN Utilities**: Docker images for various Tailscale VPN configurations:
  - `tailscale-latest`: Latest Tailscale version.
  - `tailscale-over-cloudflare-warp`: Tailscale over Cloudflare WARP.
  - `tailscale-over-adguard-home`: Tailscale over AdGuard Home.
  - `tailscale-over-gluetun`: Tailscale over Gluetun VPN.
  - `tailscale-dns-monitor`: DNS monitor for Tailscale networks.
- **SSH Keepalive**: A utility to maintain persistent SSH sessions.
- **TP-Link Proxy**: A proxy service for TP-Link devices.

## Purpose

The main objective of this repository is to provide Docker images for various personal side projects and to explore using GitHub Actions to build, test, and deploy Docker images automatically.

## Getting Started

### Prerequisites

- Docker installed on your machine.
- Basic knowledge of Docker and containerization.

### Building the Images

To build any of the Docker images locally, navigate to the respective directory and run:

```bash
docker build -t <image-name> .
```

### Using Docker Compose

Services can be defined using a `docker-compose.yml` file to orchestrate multiple containers.

## Projects Overview

### Remote Desktop Environments

- **rdesktop-debian-xfce**: Provides a Debian-based remote desktop environment with XFCE desktop.
- **rdesktop-ubuntu-kde**: Offers an Ubuntu-based remote desktop environment with KDE desktop.

### Apache Guacamole

- **guacamole-client-1.5.5** and **guacamole-server-1.5.5**: Build and deploy Apache Guacamole for remote desktop access through a web browser.

### Macless Haystack

An easy-to-use and easy-to-setup custom FindMy network without the need for a Mac or installing additional plugins. This unified solution allows you to run a FindMy network seamlessly.

- **macless-haystack-anisette**: An Anisette server for Apple authentication, essential for FindMy network operations.
- **macless-haystack-backend**: Backend services handling the core functionality of the custom FindMy network.
- **macless-haystack-frontend**: User-friendly frontend interface for interacting with the FindMy network.

#### Features

- **No Mac Required**: Operate a custom FindMy network without owning a Mac or using virtual machines.
- **Unified Projects**: Combines multiple projects to streamline setup and usage.
- **Optimized Firmware**: Includes firmware optimizations for devices to improve battery life and performance.
- **Ease of Use**: Simplifies the process by removing the need to install mail plugins or OpenHaystack itself.

#### Included Projects and Changes

- **OpenHaystack**: Stripped down to the mobile application (Android) and ESP32 firmware, combined with the FindYou project and optimized for power usage.
- **Biemster's FindMy**: Customizations in the keypair generator and a standalone Python webserver for fetching FindMy reports.
- **Positive Security's FindYou**: ESP32 firmware customized for battery optimization.
- **acalatrava's OpenHaystack Firmware Alternative**: NRF5x firmware customized for battery optimization.

#### Disclaimer

This project is for research purposes only. The use of this code is your responsibility. The authors take no responsibility and/or liability for how you choose to use any of the source code available here.

### Tailscale VPN Utilities

- **tailscale-latest**: The latest version of Tailscale VPN.
- **tailscale-over-cloudflare-warp**: Runs Tailscale over Cloudflare WARP to combine VPN and proxy services.
- **tailscale-over-adguard-home**: Integrates Tailscale with AdGuard Home for network-wide ad blocking.
- **tailscale-over-gluetun**: Uses Tailscale over Gluetun VPN for additional privacy.
- **tailscale-dns-monitor**: Monitors DNS within a Tailscale network.

### SSH Keepalive

- **ssh-keepalive**: Keeps SSH sessions alive to prevent disconnects due to inactivity.

### TP-Link Proxy

- **tplink-proxy**: A proxy service for managing TP-Link devices remotely.

## Contributing

Contributions are welcome; however, they will only be considered for merging in very rare circumstances, such as security-related updates. You are encouraged to fork the repository and use it as part of your workflow to build Docker images via GitHub.

## License

This project is licensed under the [MIT License](LICENSE).

## Acknowledgments

- Thanks to the authors of the [linuxserver/rdesktop](https://docs.linuxserver.io/images/docker-rdesktop/) for the base image used in the remote desktop offerings.
- Appreciation to the Tailscale team for their VPN solutions.
- Recognition to the Apache Guacamole project for enabling clientless remote desktop access.
- Credits to the contributors of Macless Haystack and the included projects:
  - OpenHaystack
  - Biemster's FindMy
  - Positive Security's FindYou
  - acalatrava's OpenHaystack Firmware Alternative
