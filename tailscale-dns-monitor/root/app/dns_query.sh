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
1.1.1.1
1.0.0.1
EOF
fi

# Check if the domains file exists; if not, create it with default value
if [ ! -f "$DOMAINS_FILE" ]; then
    echo "[$(date)] Creating default domains file at $DOMAINS_FILE..."
    echo "cloudflare.com" > "$DOMAINS_FILE"
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
    dns_server="$1"
    domain="$2"

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
    domain="$1"
    qtype="$2"
    output_file="$3"

    # Generate transaction ID (2 bytes)
    id_hex=$(dd if=/dev/urandom bs=1 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')

    # Flags: standard query (0x0100), recursion desired
    flags_hex="0100"

    # Counts
    qdcount_hex="0001"  # Number of questions
    ancount_hex="0000"
    nscount_hex="0000"
    arcount_hex="0000"

    # Build QNAME
    qname_hex=""
    oldIFS="$IFS"
    IFS='.'
    set -- $domain
    IFS="$oldIFS"
    for label; do
        length=${#label}
        length_hex=$(printf '%02x' "$length")
        # Convert label to hex
        label_hex=$(echo -n "$label" | xxd -p)
        qname_hex="${qname_hex}${length_hex}${label_hex}"
    done
    # Terminate QNAME with zero-length label (00)
    qname_hex="${qname_hex}00"

    # QTYPE
    case "$qtype" in
        A) qtype_hex="0001" ;;
        AAAA) qtype_hex="001c" ;;
        HTTPS|TYPE65) qtype_hex="0041" ;;
        *) echo "Unknown query type: $qtype"; return 1 ;;
    esac

    # QCLASS: IN (0x0001)
    qclass_hex="0001"

    # Combine all hex parts
    query_hex="${id_hex}${flags_hex}${qdcount_hex}${ancount_hex}${nscount_hex}${arcount_hex}${qname_hex}${qtype_hex}${qclass_hex}"

    # Convert hex to binary and write to output file
    echo "$query_hex" | xxd -r -p > "$output_file"
}

parse_dns_response() {
    response_file="$1"

    # Read bytes 3 and 4 (flags)
    flags_bytes=$(dd bs=1 skip=2 count=2 if="$response_file" 2>/dev/null | xxd -p)
    flags_byte2="${flags_bytes#??}"

    # Convert to decimal using printf
    flags_byte2_dec=$(printf '%d' "0x$flags_byte2")

    # Get RCODE (lower 4 bits)
    rcode=$((flags_byte2_dec & 0x0F))

    if [ "$rcode" -eq 0 ]; then
        return 0  # Success
    else
        return 1  # Failure
    fi
}

# Function to perform DNS over HTTPS queries
query_dns_over_https() {
    protocol="$1"
    hostname="$2"
    port="$3"
    ip="$4"
    path="$5"
    domain="$6"

    echo "[$(date)] Starting queries to DNS over HTTPS server '$hostname' for domain '$domain'"

    # Query types to test
    query_types="A AAAA HTTPS"

    for qtype in $query_types; do
        echo "[$(date)] Querying $qtype record..."
        # Create temporary files
        query_file=$(mktemp)
        response_file=$(mktemp)

        # Generate DNS query
        if ! generate_dns_query "$domain" "$qtype" "$query_file"; then
            echo "[$(date)] Failed to generate DNS query for $qtype record."
            rm -f "$query_file" "$response_file"
            continue
        fi

        # Construct URL
        url="$protocol://$hostname:$port$path"

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

    # Read DNS servers and domains, skipping empty lines and comments
    dns_servers=$(grep -v '^[[:space:]]*$' "$DNS_SERVERS_FILE" | grep -v '^#')
    domains=$(grep -v '^[[:space:]]*$' "$DOMAINS_FILE" | grep -v '^#')

    # For each DNS server
    for dns_server in $dns_servers; do
        # For each domain
        for domain in $domains; do
            query_dns "$dns_server" "$domain"
        done
    done

    # Read DNS over HTTPS servers, skipping empty lines and comments
    if [ -f "$DNS_OVER_HTTPS_SERVERS_FILE" ]; then
        dns_over_https_servers=$(grep -v '^[[:space:]]*$' "$DNS_OVER_HTTPS_SERVERS_FILE" | grep -v '^#')
    else
        dns_over_https_servers=""
    fi

    # For each DNS over HTTPS server
    echo "$dns_over_https_servers" | while read line; do
        # Skip if line is empty or a comment
        if [ -z "$line" ]; then continue; fi
        # Parse the line into variables
        protocol=$(echo "$line" | awk '{print $1}')
        hostname=$(echo "$line" | awk '{print $2}')
        port=$(echo "$line" | awk '{print $3}')
        ip=$(echo "$line" | awk '{print $4}')
        path=$(echo "$line" | awk '{print $5}')

        # For each domain
        for domain in $domains; do
            query_dns_over_https "$protocol" "$hostname" "$port" "$ip" "$path" "$domain"
        done
    done

    echo "[$(date)] DNS query cycle completed. Sleeping for $REFRESH_DURATION seconds..."
    echo "================================================================================"
    sleep "$REFRESH_DURATION"
done
