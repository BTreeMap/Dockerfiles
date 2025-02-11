FROM lscr.io/linuxserver/code-server:latest

RUN apt-get update && apt-get upgrade -y && apt-get install build-essential curl wget unzip lsb-release apt-transport-https -y
RUN curl -fsSL https://fnm.vercel.app/install | bash
RUN fnm install 22
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
RUN ubuntu_release=`lsb_release -rs` wget https://packages.microsoft.com/config/ubuntu/${ubuntu_release}/packages-microsoft-prod.deb -O packages-microsoft-prod.deb
RUN sudo dpkg -i packages-microsoft-prod.deb
RUN sudo apt-get update
RUN sudo apt-get install msopenjdk-21
