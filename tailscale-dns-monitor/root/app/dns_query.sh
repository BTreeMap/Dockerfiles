#!/bin/sh

# Set default refresh duration (in seconds)
REFRESH_DURATION="${REFRESH_DURATION:-600}"

# Set default DNS servers file path
DNS_SERVERS_FILE="${DNS_SERVERS_FILE:-/data/dns_servers.txt}"

# Set default domains file path
DOMAINS_FILE="${DOMAINS_FILE:-/data/domains.txt}"

# Check if the DNS servers file exists; if not, create it with default values
if [ ! -f "$DNS_SERVERS_FILE" ]; then
    echo "[$(date)] Creating default DNS servers file at $DNS_SERVERS_FILE..."
    cat <<EOF > "$DNS_SERVERS_FILE"
100.64.100.100
100.64.100.101
100.64.100.102
100.64.100.103
EOF
fi

# Check if the domains file exists; if not, create it with default value
if [ ! -f "$DOMAINS_FILE" ]; then
    echo "[$(date)] Creating default domains file at $DOMAINS_FILE..."
    echo "q.utoronto.ca" > "$DOMAINS_FILE"
fi

# Function to perform DNS queries with error handling
query_dns() {
    local dns_server="$1"
    local domain="$2"

    echo "[$(date)] Starting queries to DNS server '$dns_server' for domain '$domain'"

    # Perform A record query
    echo "[$(date)] Querying A record..."
    if dig @$dns_server $domain A +tries=1 +timeout=5 > /dev/null 2>&1; then
        echo "[$(date)] A record query to '$dns_server' for '$domain' succeeded."
    else
        echo "[$(date)] A record query to '$dns_server' for '$domain' failed."
    fi

    # Perform AAAA record query
    echo "[$(date)] Querying AAAA record..."
    if dig @$dns_server $domain AAAA +tries=1 +timeout=5 > /dev/null 2>&1; then
        echo "[$(date)] AAAA record query to '$dns_server' for '$domain' succeeded."
    else
        echo "[$(date)] AAAA record query to '$dns_server' for '$domain' failed."
    fi

    # Perform HTTPS (TYPE65) record query
    echo "[$(date)] Querying HTTPS (TYPE65) record..."
    if dig @$dns_server $domain HTTPS +tries=1 +timeout=5 > /dev/null 2>&1 || \
       dig @$dns_server $domain TYPE65 +tries=1 +timeout=5 > /dev/null 2>&1; then
        echo "[$(date)] HTTPS record query to '$dns_server' for '$domain' succeeded."
    else
        echo "[$(date)] HTTPS record query to '$dns_server' for '$domain' failed."
    fi

    echo "[$(date)] Completed queries to '$dns_server' for '$domain'."
    echo "--------------------------------------------------------------------------------"
}

# Main loop
while true; do
    echo "[$(date)] Starting DNS query cycle..."

    # Read DNS servers and domains, skipping empty lines
    dns_servers=$(grep -v '^\s*$' "$DNS_SERVERS_FILE")
    domains=$(grep -v '^\s*$' "$DOMAINS_FILE")

    # For each DNS server
    for dns_server in $dns_servers; do
        # For each domain
        for domain in $domains; do
            query_dns "$dns_server" "$domain"
        done
    done

    echo "[$(date)] DNS query cycle completed. Sleeping for $REFRESH_DURATION seconds..."
    echo "================================================================================"
    sleep "$REFRESH_DURATION"
done
