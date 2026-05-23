#!/usr/bin/env bash
# Print, for each running libvirt VM, its disks' immediate backingStore
# sources and any VFIO-passthrough GPU PCI devices.
set -euo pipefail
shopt -s nullglob

for cmd in virsh xmllint lspci; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "error: $cmd not found" >&2
        exit 1
    fi
done

mapfile -t vms < <(virsh list --state-running --name | sed '/^$/d')

if [ "${#vms[@]}" -eq 0 ]; then
    echo "no running VMs"
    exit 0
fi

xp_hostdev='/domain/devices/hostdev[@type="pci" and driver/@name="vfio"]'

for vm in "${vms[@]}"; do
    xml=$(virsh dumpxml "$vm")
    echo "${vm}:"

    sources=$(printf '%s' "$xml" \
        | xmllint --xpath '/domain/devices/disk/backingStore/source' - 2>/dev/null \
        || true)
    mapfile -t backings < <(
        printf '%s' "$sources" \
            | grep -oE '(file|dev|name|volume)="[^"]*"' \
            | sed -E 's/^[^=]+="([^"]*)"$/\1/'
    )
    if [ "${#backings[@]}" -eq 0 ]; then
        echo "  backing: <none>"
    else
        for path in "${backings[@]}"; do
            echo "  backing: ${path}"
        done
    fi

    count=$(printf '%s' "$xml" \
        | xmllint --xpath "count(${xp_hostdev})" - 2>/dev/null \
        || echo 0)

    gpu_found=0
    for i in $(seq 1 "$count"); do
        dom=$(printf '%s' "$xml" | xmllint --xpath "string(${xp_hostdev}[$i]/source/address/@domain)" - 2>/dev/null)
        bus=$(printf '%s' "$xml" | xmllint --xpath "string(${xp_hostdev}[$i]/source/address/@bus)" - 2>/dev/null)
        slot=$(printf '%s' "$xml" | xmllint --xpath "string(${xp_hostdev}[$i]/source/address/@slot)" - 2>/dev/null)
        func=$(printf '%s' "$xml" | xmllint --xpath "string(${xp_hostdev}[$i]/source/address/@function)" - 2>/dev/null)

        addr=$(printf "%04x:%02x:%02x.%x" "$dom" "$bus" "$slot" "$func")
        info=$(lspci -nns "$addr" 2>/dev/null || true)

        if [[ "$info" =~ \[03[0-9a-f]{2}\] ]]; then
            vendor_id=$(printf '%s' "$info" | grep -oE '\[[0-9a-f]{4}:[0-9a-f]{4}\]' | head -n1)
            desc=$(printf '%s' "$info" \
                | sed -E 's/^[^ ]+ [^[]*\[03[0-9a-f]{2}\]: //' \
                | sed -E "s/ ${vendor_id//\[/\\[}//")

            dsn=$(lspci -vv -s "$addr" 2>/dev/null \
                | grep -oE 'Device Serial Number [0-9a-f-]+' \
                | awk '{print $NF}')
            [ -z "$dsn" ] && dsn="<n/a>"

            phys_slot="<n/a>"
            slot_target="${addr%.*}"
            for slot_dir in /sys/bus/pci/slots/*/; do
                [ -r "${slot_dir}address" ] || continue
                if [ "$(<"${slot_dir}address")" = "$slot_target" ]; then
                    phys_slot="${slot_dir%/}"
                    phys_slot="${phys_slot##*/}"
                    break
                fi
            done

            echo "  gpu:     ${addr} ${vendor_id} ${desc}"
            echo "           slot=${phys_slot} dsn=${dsn}"
            gpu_found=1
        fi
    done

    if [ "$gpu_found" -eq 0 ]; then
        echo "  gpu:     <none>"
    fi
done
