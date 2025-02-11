FROM lscr.io/linuxserver/code-server:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV PUID=1000
ENV PGID=1000
ENV TZ=America/Toronto
ENV DEFAULT_WORKSPACE=/config/workspace

RUN apt-get update && apt-get upgrade -y && apt-get install build-essential curl wget unzip lsb-release apt-transport-https -y
RUN curl -fsSL https://fnm.vercel.app/install | bash
RUN . /config/.bashrc
RUN fnm install 22
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
RUN wget https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb
RUN dpkg -i packages-microsoft-prod.deb
RUN apt-get update
RUN apt-get install msopenjdk-21
RUN rm packages-microsoft-prod.deb
RUN apt-get clean && \
    rm -rf \
    /config/* \
    /tmp/* \
    /var/lib/apt/lists/* \
    /var/tmp/*
