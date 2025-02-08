# Docker Images for Side Projects

This repository contains Dockerfiles and GitHub Actions workflows for building Docker images for various side projects. **All images are automatically built and uploaded to the GitHub Container Registry (GHCR) with tags like `ghcr.io/btreemap/dockerfiles:guacamole-client-1.5.5`**. Users are encouraged to run these pre-built images instead of building them locally, as they are updated at least twice a day to provide the latest environment, features, and dependencies.

The projects include:

- **Remote Desktop Environments**: Docker images providing remote desktop access with different desktop environments:
  - `rdesktop-debian-xfce`: Debian with XFCE desktop.
  - `rdesktop-ubuntu-kde`: Ubuntu with KDE desktop.
  > **Java Development Support**: Integration of Microsoft Build of OpenJDK version 21 for Java applications.
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

### Using the Pre-built Images

**All Docker images are built and uploaded to the GitHub Container Registry (GHCR) with tags like `ghcr.io/btreemap/dockerfiles:<image-name>`**.

You can pull and run the images directly:

```bash
docker run ghcr.io/btreemap/dockerfiles:<image-name>
```

For example, to run the Apache Guacamole client version 1.5.5:

```bash
docker run ghcr.io/btreemap/dockerfiles:guacamole-client-1.5.5
```

**These images are updated at least twice a day**, ensuring you have access to the latest environment, features, and dependencies.

### Using Docker Compose

You can also use the images in your `docker-compose.yml` file by specifying the image:

```yaml
services:
  guacamole-client:
    image: ghcr.io/btreemap/dockerfiles:guacamole-client-1.5.5
    ports:
      - "8080:8080"
```

## Projects Overview

### Remote Desktop Environments

- **rdesktop-debian-xfce**: Provides a Debian-based remote desktop environment with XFCE desktop.
  - **Image**: `ghcr.io/btreemap/dockerfiles:rdesktop-debian-xfce`
- **rdesktop-ubuntu-kde**: Offers an Ubuntu-based remote desktop environment with KDE desktop.
  - **Image**: `ghcr.io/btreemap/dockerfiles:rdesktop-ubuntu-kde`

### Apache Guacamole

- **guacamole-client-1.5.5** and **guacamole-server-1.5.5**: Build and deploy Apache Guacamole for remote desktop access through a web browser.
  - **Client Image**: `ghcr.io/btreemap/dockerfiles:guacamole-client-1.5.5`
  - **Server Image**: `ghcr.io/btreemap/dockerfiles:guacamole-server-1.5.5`

### Macless Haystack

An easy-to-use and easy-to-setup custom FindMy network without the need for a Mac or installing additional plugins. This unified solution allows you to run a FindMy network seamlessly.

- **macless-haystack-anisette**: An Anisette server for Apple authentication, essential for FindMy network operations.
  - **Image**: `ghcr.io/btreemap/dockerfiles:macless-haystack-anisette`
- **macless-haystack-backend**: Backend services handling the core functionality of the custom FindMy network.
  - **Image**: `ghcr.io/btreemap/dockerfiles:macless-haystack-backend`
- **macless-haystack-frontend**: User-friendly frontend interface for interacting with the FindMy network.
  - **Image**: `ghcr.io/btreemap/dockerfiles:macless-haystack-frontend`

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
  - **Image**: `ghcr.io/btreemap/dockerfiles:tailscale-latest`
- **tailscale-over-cloudflare-warp**: Runs Tailscale over Cloudflare WARP to combine VPN and proxy services.
  - **Image**: `ghcr.io/btreemap/dockerfiles:tailscale-over-cloudflare-warp`
- **tailscale-over-adguard-home**: Integrates Tailscale with AdGuard Home for network-wide ad blocking.
  - **Image**: `ghcr.io/btreemap/dockerfiles:tailscale-over-adguard-home`
- **tailscale-over-gluetun**: Uses Tailscale over Gluetun VPN for additional privacy.
  - **Image**: `ghcr.io/btreemap/dockerfiles:tailscale-over-gluetun`
- **tailscale-dns-monitor**: Monitors DNS within a Tailscale network.
  - **Image**: `ghcr.io/btreemap/dockerfiles:tailscale-dns-monitor`

### SSH Keepalive

- **ssh-keepalive**: Keeps SSH sessions alive to prevent disconnects due to inactivity.
  - **Image**: `ghcr.io/btreemap/dockerfiles:ssh-keepalive`

### TP-Link Proxy

- **tplink-proxy**: A proxy service for managing TP-Link devices remotely.
  - **Image**: `ghcr.io/btreemap/dockerfiles:tplink-proxy`

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
