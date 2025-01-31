#!/bin/sh

# Set default refresh duration (in seconds)
REFRESH_DURATION="${REFRESH_DURATION:-600}"

# Set default DNS servers file path
DNS_SERVERS_FILE="${DNS_SERVERS_FILE:-/data/dns_servers.txt}"

# Set default domains file path
DOMAINS_FILE="${DOMAINS_FILE:-/data/domains.txt}"

# Set default DNS over HTTPS servers file path
DNS_OVER_HTTPS_SERVERS_FILE="${DNS_OVER_HTTPS_SERVERS_FILE:-/data/dns_over_https_servers.txt}"

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

# Check if the DNS over HTTPS servers file exists; if not, create it with default values
if [ ! -f "$DNS_OVER_HTTPS_SERVERS_FILE" ]; then
    echo "[$(date)] Creating default DNS over HTTPS servers file at $DNS_OVER_HTTPS_SERVERS_FILE..."
    cat <<EOF > "$DNS_OVER_HTTPS_SERVERS_FILE"
https one.one.one.one 443 1.1.1.1 /dns-query
EOF
fi

# Function to perform DNS queries with error handling
query_dns() {
    local dns_server="$1"
    local domain="$2"

    echo "[$(date)] Starting queries to DNS server '$dns_server' for domain '$domain'"

    # Perform A record query
    echo "[$(date)] Querying A record..."
    if dig @"$dns_server" "$domain" A +tries=1 +timeout=5 > /dev/null 2>&1; then
        echo "[$(date)] A record query to '$dns_server' for '$domain' succeeded."
    else
        echo "[$(date)] A record query to '$dns_server' for '$domain' failed."
    fi

    # Perform AAAA record query
    echo "[$(date)] Querying AAAA record..."
    if dig @"$dns_server" "$domain" AAAA +tries=1 +timeout=5 > /dev/null 2>&1; then
        echo "[$(date)] AAAA record query to '$dns_server' for '$domain' succeeded."
    else
        echo "[$(date)] AAAA record query to '$dns_server' for '$domain' failed."
    fi

    # Perform HTTPS (TYPE65) record query
    echo "[$(date)] Querying HTTPS (TYPE65) record..."
    if dig @"$dns_server" "$domain" HTTPS +tries=1 +timeout=5 > /dev/null 2>&1 || \
       dig @"$dns_server" "$domain" TYPE65 +tries=1 +timeout=5 > /dev/null 2>&1; then
        echo "[$(date)] HTTPS record query to '$dns_server' for '$domain' succeeded."
    else
        echo "[$(date)] HTTPS record query to '$dns_server' for '$domain' failed."
    fi

    echo "[$(date)] Completed queries to '$dns_server' for '$domain'."
    echo "--------------------------------------------------------------------------------"
}

# Function to generate DNS query in wire format
generate_dns_query() {
    local domain="$1"
    local qtype="$2"
    local output_file="$3"

    # Generate a random ID
    local id_hex
    id_hex=$(openssl rand -hex 2)
    local id="\x${id_hex:0:2}\x${id_hex:2:2}"

    # Flags: standard query, recursion desired (0x0100)
    local flags="\x01\x00"

    # QDCOUNT: number of questions (1)
    local qdcount="\x00\x01"

    # ANCOUNT, NSCOUNT, ARCOUNT: zero
    local ancount="\x00\x00"
    local nscount="\x00\x00"
    local arcount="\x00\x00"

    # Build QNAME
    local qname=""
    IFS='.' read -ra labels <<< "$domain"
    for label in "${labels[@]}"; do
        local len=${#label}
        qname="${qname}$(printf "\\x%02x" "$len")$label"
    done
    # Terminate QNAME with zero length
    qname="${qname}\x00"

    # QTYPE
    declare -A qtypes
    qtypes=( ["A"]="\x00\x01" ["AAAA"]="\x00\x1c" ["HTTPS"]="\x00\x41" ["TYPE65"]="\x00\x41" )

    # Get the QTYPE code
    local qtype_code="${qtypes[$qtype]}"
    if [ -z "$qtype_code" ]; then
        echo "Unknown query type: $qtype"
        return 1
    fi

    # QCLASS: IN (0x0001)
    local qclass="\x00\x01"

    # Combine all parts
    local query="${id}${flags}${qdcount}${ancount}${nscount}${arcount}${qname}${qtype_code}${qclass}"

    # Write to output file
    printf "$query" > "$output_file"
}

# Function to parse DNS response and check for success
parse_dns_response() {
    local response_file="$1"

    # Read the fourth byte (byte offset 3, zero-based indexing)
    local flags_byte2_hex
    flags_byte2_hex=$(xxd -s 3 -l 1 -p "$response_file")

    # Convert to decimal
    local flags_byte2_dec=$((16#$flags_byte2_hex))

    # Get RCODE (lower 4 bits)
    local rcode=$((flags_byte2_dec & 0x0F))

    if [ "$rcode" -eq 0 ]; then
        return 0  # Success
    else
        return 1  # Failure
    fi
}

# Function to perform DNS over HTTPS queries
query_dns_over_https() {
    local protocol="$1"
    local hostname="$2"
    local port="$3"
    local ip="$4"
    local path="$5"
    local domain="$6"

    echo "[$(date)] Starting queries to DNS over HTTPS server '$hostname' for domain '$domain'"

    # Query types to test
    local query_types=("A" "AAAA" "HTTPS")

    for qtype in "${query_types[@]}"; do
        echo "[$(date)] Querying $qtype record..."
        # Create temporary files
        local query_file
        local response_file
        query_file=$(mktemp)
        response_file=$(mktemp)

        # Generate DNS query
        if ! generate_dns_query "$domain" "$qtype" "$query_file"; then
            echo "[$(date)] Failed to generate DNS query for $qtype record."
            rm -f "$query_file" "$response_file"
            continue
        fi

        # Construct URL
        local url="$protocol://$hostname:$port$path"

        # Use curl to send the query
        if curl -s --fail -o "$response_file" --data-binary "@$query_file" \
            -H 'Content-Type: application/dns-message' \
            --resolve "$hostname:$port:$ip" "$url"; then
            # Parse the response
            if parse_dns_response "$response_file"; then
                echo "[$(date)] $qtype record query to '$hostname' for '$domain' succeeded."
            else
                echo "[$(date)] $qtype record query to '$hostname' for '$domain' failed (DNS error)."
            fi
        else
            echo "[$(date)] $qtype record query to '$hostname' for '$domain' failed (curl error)."
        fi

        # Remove temporary files
        rm -f "$query_file" "$response_file"
    done

    echo "[$(date)] Completed queries to '$hostname' for '$domain'."
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

    # Read DNS over HTTPS servers, skipping empty lines
    if [ -f "$DNS_OVER_HTTPS_SERVERS_FILE" ]; then
        dns_over_https_servers=$(grep -v '^\s*$' "$DNS_OVER_HTTPS_SERVERS_FILE")
    else
        dns_over_https_servers=""
    fi

    # For each DNS over HTTPS server
    echo "$dns_over_https_servers" | while IFS=' ' read -r protocol hostname port ip path; do
        # Skip if line is empty
        if [ -z "$protocol" ]; then continue; fi
        # For each domain
        for domain in $domains; do
            query_dns_over_https "$protocol" "$hostname" "$port" "$ip" "$path" "$domain"
        done
    done

    echo "[$(date)] DNS query cycle completed. Sleeping for $REFRESH_DURATION seconds..."
    echo "================================================================================"
    sleep "$REFRESH_DURATION"
done
