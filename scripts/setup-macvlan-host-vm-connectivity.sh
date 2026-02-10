#!/bin/bash
set -e

# Script to configure macvlan interface for host-to-VM connectivity
# This allows the GPU host to communicate with VMs using macvtap networking
# Works on Ubuntu 22.04+ with netplan
#
# The subnet is auto-detected from the host's primary IP.
#
# Usage: sudo ./setup-macvlan-host-vm-connectivity.sh -r start-end -o offset [-i interface]

echo "=== Setting up macvlan interface for host-VM connectivity ==="

# Configurable via flags
PARENT_INTERFACE=""
VM_POOL_START=""
VM_POOL_END=""
MACVLAN_IP_OFFSET=""

# Fixed
MACVLAN_INTERFACE="macvlan0"
SUBNET_MASK="24"
SETUP_SCRIPT="/usr/local/bin/setup-macvlan.sh"
SERVICE_FILE="/etc/systemd/system/macvlan-setup.service"

usage() {
    echo "Usage: sudo $0 -r start-end -o offset [-i interface]"
    echo ""
    echo "Required:"
    echo "  -r START-END   VM IP pool range, e.g. 200-250"
    echo "  -o OFFSET      Macvlan IP offset added to host's last octet, e.g. 50"
    echo ""
    echo "Optional:"
    echo "  -i INTERFACE   Parent network interface (default: auto-detect from default route)"
    echo "  -h             Show this help"
    exit 1
}

while getopts "i:r:o:h" opt; do
    case $opt in
        i) PARENT_INTERFACE="$OPTARG" ;;
        r)
            VM_POOL_START=$(echo "$OPTARG" | cut -d'-' -f1)
            VM_POOL_END=$(echo "$OPTARG" | cut -d'-' -f2)
            if ! [[ "$VM_POOL_START" =~ ^[0-9]+$ ]] || ! [[ "$VM_POOL_END" =~ ^[0-9]+$ ]]; then
                echo "Error: Invalid range format. Use START-END, e.g. 200-250"
                exit 1
            fi
            ;;
        o) MACVLAN_IP_OFFSET="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$VM_POOL_START" || -z "$VM_POOL_END" ]]; then
    echo "Error: -r is required"
    usage
fi

if [[ -z "$MACVLAN_IP_OFFSET" ]]; then
    echo "Error: -o is required"
    usage
fi

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "Error: This script must be run as root (use sudo)"
   exit 1
fi

# Auto-discover parent interface via default route if not specified
if [[ -z "$PARENT_INTERFACE" ]]; then
    echo "Auto-discovering network configuration..."
    PARENT_INTERFACE=$(ip route show default | awk '{print $5}' | head -1)
fi

if [[ -z "$PARENT_INTERFACE" ]]; then
    echo "Error: Could not determine default network interface"
    echo "Please check your network configuration with: ip route show"
    echo "Or specify one with: $0 -i <interface>"
    exit 1
fi

# Auto-detect host IP and subnet from the parent interface
HOST_IP=$(ip -4 addr show dev "$PARENT_INTERFACE" | grep -oP 'inet \K[0-9.]+' | head -1)

if [[ -z "$HOST_IP" ]]; then
    echo "Error: Could not determine host IP on interface $PARENT_INTERFACE"
    exit 1
fi

VM_SUBNET=$(echo "$HOST_IP" | cut -d'.' -f1-3)
LAST_OCTET=$(echo "$HOST_IP" | cut -d'.' -f4)
MACVLAN_IP="${VM_SUBNET}.$((MACVLAN_IP_OFFSET + LAST_OCTET))"

echo "✓ Detected parent interface: $PARENT_INTERFACE"
echo "✓ Host IP: $HOST_IP"
echo "✓ Macvlan interface: $MACVLAN_INTERFACE"
echo "✓ Macvlan IP: $MACVLAN_IP/$SUBNET_MASK"
echo ""

# Create the setup script that will run on boot
echo "Creating macvlan setup script..."
cat > $SETUP_SCRIPT <<EOF
#!/bin/bash
set -e

# Wait for parent interface to be ready
sleep 2

# Remove existing macvlan interface if it exists
ip link delete $MACVLAN_INTERFACE 2>/dev/null || true

# Create macvlan interface
ip link add link $PARENT_INTERFACE name $MACVLAN_INTERFACE type macvlan mode bridge
ip addr add $MACVLAN_IP/$SUBNET_MASK dev $MACVLAN_INTERFACE
ip link set $MACVLAN_INTERFACE up

# Add routes for VM/container pool (${VM_SUBNET}.${VM_POOL_START}-${VM_POOL_END})
for i in \$(seq $VM_POOL_START $VM_POOL_END); do
    # Skip host IP and macvlan IP
    if [ "\$i" != "$LAST_OCTET" ] && [ "\$i" != "$((MACVLAN_IP_OFFSET + LAST_OCTET))" ]; then
        ip route add ${VM_SUBNET}.\$i/32 dev $MACVLAN_INTERFACE 2>/dev/null || true
    fi
done

echo "Macvlan interface $MACVLAN_INTERFACE configured successfully"
EOF

chmod +x $SETUP_SCRIPT
echo "✓ Created: $SETUP_SCRIPT"

# Create systemd service
echo "Creating systemd service..."
cat > $SERVICE_FILE <<EOF
[Unit]
Description=Setup macvlan interface for VM connectivity
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$SETUP_SCRIPT
ExecStop=/usr/sbin/ip link delete $MACVLAN_INTERFACE

[Install]
WantedBy=multi-user.target
EOF

echo "✓ Created: $SERVICE_FILE"

# Enable and start the service
echo ""
echo "Enabling and starting macvlan-setup service..."
systemctl daemon-reload
systemctl enable macvlan-setup.service
systemctl start macvlan-setup.service

# Wait a moment for the interface to come up
sleep 2

# Verify the interface exists
echo ""
echo "Verifying macvlan interface..."
if ip link show $MACVLAN_INTERFACE &> /dev/null; then
    echo "✓ Interface $MACVLAN_INTERFACE created successfully"
    echo ""
    ip addr show $MACVLAN_INTERFACE
else
    echo "✗ Error: Interface $MACVLAN_INTERFACE not found"
    echo "Check service status: sudo systemctl status macvlan-setup.service"
    exit 1
fi

# Show routes
echo ""
echo "Routes through $MACVLAN_INTERFACE:"
ip route show | grep $MACVLAN_INTERFACE

echo ""
echo "=== Setup complete! ==="
echo ""
echo "✓ Macvlan interface is configured and will persist across reboots"
echo "✓ All VMs/containers in the range ${VM_SUBNET}.${VM_POOL_START}-${VM_POOL_END} are now reachable from the host"
echo ""
echo "Service management:"
echo "  sudo systemctl status macvlan-setup.service"
echo "  sudo systemctl restart macvlan-setup.service"
echo "  sudo journalctl -u macvlan-setup.service"
