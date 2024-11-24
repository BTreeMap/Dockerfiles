# Docker Images for Side Projects

This repository contains Dockerfiles and GitHub Actions configurations for building Docker images for various side projects. Currently, it offers Docker images based on [linuxserver/rdesktop](https://docs.linuxserver.io/images/docker-rdesktop/) with `msopenjdk-21` included. Additional images for other applications will be added as needed.

## Purpose

The main objective of this repository is to explore and demonstrate the procedures for using GitHub Actions to build, test, and deploy Docker images.

## Current Features

- **Docker Image for Remote Desktop**: An image using `linuxserver/rdesktop` configured for remote desktop access.
- **Java Development Support**: Integration of Microsoft Build of OpenJDK version 21 for Java applications.

## Getting Started

### Prerequisites

- Docker installed on your machine.
- Basic knowledge of Docker and containerization.

### Building the Images

To build the Docker images locally, you can use the following command in the root of the repository:

```bash
docker build -t <image-name> -f <path-to-Dockerfile> .
```

### Using Docker Compose

If preferred, services can be defined using a `docker-compose.yml` file.

## Contributing

Contributions are welcome; however, they will only be considered for merging in very rare circumstances, such as security-related updates. You are encouraged to fork the repository and use it as part of your workflow to build multiple Docker images on GitHub.

## License

This project is licensed under the [MIT License](LICENSE).

## Acknowledgments

Thanks to the authors of the [linuxserver/rdesktop](https://docs.linuxserver.io/images/docker-rdesktop/) for the base image used in the current offerings.
