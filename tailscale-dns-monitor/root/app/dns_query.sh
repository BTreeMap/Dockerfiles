#!/bin/sh

# Set default refresh duration (in seconds)
REFRESH_DURATION="${REFRESH_DURATION:-600}"

# Set default DNS servers file path
DNS_SERVERS_FILE="${DNS_SERVERS_FILE:-/data/dns_servers.txt}"

# Set default DoH servers file path
DOH_SERVERS_FILE="${DOH_SERVERS_FILE:-/data/doh_servers.txt}"

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

# Check if the DoH servers file exists; if not, create it with default values
if [ ! -f "$DOH_SERVERS_FILE" ]; then
    echo "[$(date)] Creating default DoH servers file at $DOH_SERVERS_FILE..."
    cat <<EOF > "$DOH_SERVERS_FILE"
https one.one.one.one 443 1.1.1.1 /dns-query
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

# Function to perform DoH queries
query_doh() {
    local protocol="$1"
    local hostname="$2"
    local port="$3"
    local ip="$4"
    local path="$5"
    local domain="$6"

    echo "[$(date)] Starting DoH queries to server '$hostname' ($ip) for domain '$domain'"

    # Query A record
    echo "[$(date)] Querying A record via DoH..."
    if generate_and_send_doh_query "$domain" "A" "$protocol" "$hostname" "$port" "$ip" "$path"; then
        echo "[$(date)] A record query via DoH to '$hostname' for '$domain' succeeded."
    else
        echo "[$(date)] A record query via DoH to '$hostname' for '$domain' failed."
    fi

    # Query AAAA record
    echo "[$(date)] Querying AAAA record via DoH..."
    if generate_and_send_doh_query "$domain" "AAAA" "$protocol" "$hostname" "$port" "$ip" "$path"; then
        echo "[$(date)] AAAA record query via DoH to '$hostname' for '$domain' succeeded."
    else
        echo "[$(date)] AAAA record query via DoH to '$hostname' for '$domain' failed."
    fi

    # Query HTTPS (TYPE65) record
    echo "[$(date)] Querying HTTPS record via DoH..."
    if generate_and_send_doh_query "$domain" "HTTPS" "$protocol" "$hostname" "$port" "$ip" "$path"; then
        echo "[$(date)] HTTPS record query via DoH to '$hostname' for '$domain' succeeded."
    else
        echo "[$(date)] HTTPS record query via DoH to '$hostname' for '$domain' failed."
    fi

    echo "[$(date)] Completed DoH queries to '$hostname' for '$domain'."
    echo "--------------------------------------------------------------------------------"
}

# Helper function to generate and send DoH query
generate_and_send_doh_query() {
    local domain="$1"
    local qtype="$2"
    local protocol="$3"
    local hostname="$4"
    local port="$5"
    local ip="$6"
    local path="$7"

    # Generate the DNS query in wireformat
    local hex_header="000001000001000000000000"  # ID 0, flags RD=1, QDCOUNT=1, others 0

    # Split domain into labels and encode
    local hex_qname=""
    IFS='.' read -ra labels <<< "$domain"
    for label in "${labels[@]}"; do
        len=$(printf "%02x" "${#label}")
        hex_label=$(echo -n "$label" | xxd -p | tr -d '\n')
        hex_qname+="${len}${hex_label}"
    done
    hex_qname+="00"  # End of QNAME

    # Determine QTYPE
    case "$qtype" in
        A)
            qtype_hex="0001"
            ;;
        AAAA)
            qtype_hex="001c"
            ;;
        HTTPS|TYPE65)
            qtype_hex="0041"
            ;;
        *)
            echo "Unsupported query type $qtype"
            return 1
            ;;
    esac

    local qclass_hex="0001"  # IN class

    local full_hex="${hex_header}${hex_qname}${qtype_hex}${qclass_hex}"

    # Convert hex to binary
    local query_file=$(mktemp)
    if ! echo -n "$full_hex" | xxd -r -p > "$query_file"; then
        echo "[$(date)] Failed to generate query for $qtype"
        rm -f "$query_file"
        return 1
    fi

    # Send via curl
    local url="${protocol}://${hostname}:${port}${path}"
    local response_file=$(mktemp)

    # Use --resolve to connect to the specified IP
    if ! curl -s -o "$response_file" --resolve "${hostname}:${port}:${ip}" -H "Content-Type: application/dns-message" --data-binary "@$query_file" "$url" > /dev/null 2>&1; then
        echo "[$(date)] Curl request failed for $qtype"
        rm -f "$query_file" "$response_file"
        return 1
    fi

    # Check response
    local success=0
    if [ -s "$response_file" ]; then
        # Parse DNS response
        local flags_hex=$(dd if="$response_file" bs=1 count=2 skip=2 2>/dev/null | xxd -p | tr -d '\n')
        local byte2=$(echo "$flags_hex" | cut -c1-2)
        local byte3=$(echo "$flags_hex" | cut -c3-4)
        local qr_bit=$((0x$byte2 & 0x80))
        local rcode=$((0x$byte3 & 0x0F))
        local ancount_hex=$(dd if="$response_file" bs=1 count=2 skip=6 2>/dev/null | xxd -p | tr -d '\n')
        local ancount=$((0x$ancount_hex))

        if [ $qr_bit -eq 128 ] && [ $rcode -eq 0 ] && [ $ancount -gt 0 ]; then
            success=1
        fi
    else
        echo "[$(date)] Empty response for $qtype"
    fi

    rm -f "$query_file" "$response_file"
    return $((1 - success))
}

# Main loop
while true; do
    echo "[$(date)] Starting DNS query cycle..."

    # Read DNS servers, DoH servers, and domains, skipping empty lines and comments
    dns_servers=$(grep -v '^#' "$DNS_SERVERS_FILE" | grep -v '^\s*$')
    doh_servers=$(grep -v '^#' "$DOH_SERVERS_FILE" | grep -v '^\s*$')
    domains=$(grep -v '^#' "$DOMAINS_FILE" | grep -v '^\s*$')

    # For each DNS server
    for dns_server in $dns_servers; do
        for domain in $domains; do
            query_dns "$dns_server" "$domain"
        done
    done

    # For each DoH server
    echo "$doh_servers" | while IFS= read -r line; do
        protocol=$(echo "$line" | awk '{print $1}')
        hostname=$(echo "$line" | awk '{print $2}')
        port=$(echo "$line" | awk '{print $3}')
        ip=$(echo "$line" | awk '{print $4}')
        path=$(echo "$line" | awk '{$1=$2=$3=$4=""; print $0}' | sed -e 's/^ *//' -e 's/ *$//')

        for domain in $domains; do
            query_doh "$protocol" "$hostname" "$port" "$ip" "$path" "$domain"
        done
    done

    echo "[$(date)] DNS query cycle completed. Sleeping for $REFRESH_DURATION seconds..."
    echo "================================================================================"
    sleep "$REFRESH_DURATION"
done