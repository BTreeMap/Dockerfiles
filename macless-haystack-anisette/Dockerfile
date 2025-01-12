FROM dadoum/anisette-v3-server:latest

# Switch to root user to perform the upgrade
USER root

# Update package lists and upgrade all packages
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Switch back to the non-root user
USER Alcoholic

# (Optional) Specify the entrypoint if needed
# ENTRYPOINT [ "/opt/anisette-v3-server" ]
