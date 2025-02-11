FROM lscr.io/linuxserver/code-server:latest

RUN apt-get update && apt-get upgrade -y && apt-get install build-essential curl wget unzip -y
RUN curl -fsSL https://fnm.vercel.app/install | bash
RUN fnm install 22

