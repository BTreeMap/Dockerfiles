FROM lscr.io/linuxserver/rdesktop:arm64v8-debian-xfce
ENV MS_PACKAGES_DEB_PATH="/tmp/.docker/packages-microsoft-prod.deb"
RUN apt update
RUN apt install wget lsb-release -y
RUN wget https://packages.microsoft.com/config/debian/$(lsb_release -rs)/packages-microsoft-prod.deb -O $MS_PACKAGES_DEB_PATH
RUN dpkg -i $MS_PACKAGES_DEB_PATH
RUN rm $MS_PACKAGES_DEB_PATH
RUN apt update
RUN mkdir -p /usr/share/man/man1
RUN apt install msopenjdk-21
