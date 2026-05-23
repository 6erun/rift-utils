#!/usr/bin/env bash
# switch_gpu_driver.sh — Manually switch NVIDIA GPUs between the nvidia and vfio-pci drivers.
#
# Mirrors the logic in workspace/lib/provider/src/engine/gpu_manager.rs
# (switch_to_vfio / switch_to_nvidia) so you can perform the same operation by hand,
# e.g. when debugging a host outside the rift-desktop service.
#
# Usage:
#   sudo ./switch_gpu_driver.sh vfio    <bdf> [<bdf>...]
#   sudo ./switch_gpu_driver.sh nvidia  <bdf> [<bdf>...] [--verify-cuda]
#   sudo ./switch_gpu_driver.sh status  <bdf> [<bdf>...]
#
# BDFs may be given as `0000:65:00.0`, `65:00.0`, or `65:00` — the function part
# is ignored; the script always operates on `.0` (GPU) and `.1` (audio, if present).

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "must run as root (sysfs writes require it)"
}

# Normalise a user-supplied BDF to `DDDD:BB:DD.F`.
normalize_bdf() {
    local in="$1"
    in="${in,,}"  # lowercase
    # Strip any leading function and re-add explicitly.
    # Accept: 0000:65:00.0, 65:00.0, 0000:65:00, 65:00
    local domain bus dev func
    if [[ "$in" =~ ^([0-9a-f]{4}):([0-9a-f]{2}):([0-9a-f]{2})(\.([0-9a-f]))?$ ]]; then
        domain="${BASH_REMATCH[1]}"; bus="${BASH_REMATCH[2]}"
        dev="${BASH_REMATCH[3]}";    func="${BASH_REMATCH[5]:-0}"
    elif [[ "$in" =~ ^([0-9a-f]{2}):([0-9a-f]{2})(\.([0-9a-f]))?$ ]]; then
        domain="0000"; bus="${BASH_REMATCH[1]}"
        dev="${BASH_REMATCH[2]}"; func="${BASH_REMATCH[4]:-0}"
    else
        die "invalid BDF: $1 (expected DDDD:BB:DD.F or BB:DD.F)"
    fi
    if [[ "$func" != "0" ]]; then
        warn "ignoring non-zero function in $1 (script always uses .0 / .1)"
    fi
    printf '%s:%s:%s' "$domain" "$bus" "$dev"
}

bdf_with_func() { printf '%s.%s' "$1" "$2"; }

device_exists()  { [[ -e "/sys/bus/pci/devices/$1" ]]; }
current_driver() {
    local link="/sys/bus/pci/devices/$1/driver"
    [[ -L "$link" ]] || { echo "none"; return; }
    basename "$(readlink "$link")"
}

unbind_if_bound() {
    local bdf="$1"
    local drv_link="/sys/bus/pci/devices/$bdf/driver"
    if [[ -L "$drv_link" ]]; then
        log "unbinding $bdf from $(basename "$(readlink "$drv_link")")"
        echo "$bdf" > "$drv_link/unbind"
    fi
    # Clear driver_override so a later bind to a different driver isn't rejected.
    local override="/sys/bus/pci/devices/$bdf/driver_override"
    [[ -e "$override" ]] && echo "" > "$override"
}

bind_to_driver() {
    local bdf="$1" driver="$2"
    local override="/sys/bus/pci/devices/$bdf/driver_override"
    local bind="/sys/bus/pci/drivers/$driver/bind"
    [[ -e "$bind" ]] || die "driver '$driver' not loaded (no $bind)"
    echo "$driver" > "$override"
    echo "$bdf"    > "$bind"
    log "bound $bdf to $driver"
}

ensure_module() {
    local mod="$1"
    log "modprobe $mod"
    modprobe "$mod" || die "modprobe $mod failed"
}

try_unload_modules() {
    # Best-effort: matches try_unload_nvidia_modules() in nvidia_ctl::utils.
    for mod in nvidia_peermem nvidia_drm; do
        if modprobe -r "$mod" 2>/dev/null; then
            log "unloaded $mod"
        fi
    done
}

# Check no compute process holds the listed GPUs.
ensure_gpu_available() {
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        warn "nvidia-smi not available; skipping compute-process check"
        return
    fi
    local in_use=""
    # `nvidia-smi --query-compute-apps=pid,gpu_bus_id --format=csv,noheader`
    while IFS=, read -r pid bus_id; do
        pid="${pid// /}"; bus_id="${bus_id// /}"; bus_id="${bus_id,,}"
        # nvidia-smi prints full BDF (0000:65:00.0); we hold normalized roots (0000:65:00).
        for root in "$@"; do
            if [[ "$bus_id" == "$root".* || "$bus_id" == "$root" ]]; then
                in_use+="  PID $pid on $bus_id"$'\n'
            fi
        done
    done < <(nvidia-smi --query-compute-apps=pid,gpu_bus_id --format=csv,noheader 2>/dev/null || true)

    if [[ -n "$in_use" ]]; then
        die "Compute processes are using the target GPUs. Stop them and retry:"$'\n'"$in_use"
    fi

    # DRM node check via lsof for non-compute users (display servers, etc.).
    if command -v lsof >/dev/null 2>&1; then
        local drm_nodes=()
        for root in "$@"; do
            local drm_dir="/sys/bus/pci/devices/${root}.0/drm"
            [[ -d "$drm_dir" ]] || continue
            for entry in "$drm_dir"/card* "$drm_dir"/renderD*; do
                [[ -e "$entry" ]] || continue
                drm_nodes+=("/dev/dri/$(basename "$entry")")
            done
        done
        if (( ${#drm_nodes[@]} > 0 )); then
            local out
            out=$(lsof -w "${drm_nodes[@]}" 2>/dev/null || true)
            if [[ -n "$out" && $(echo "$out" | wc -l) -gt 1 ]]; then
                die "Processes are using GPU DRM nodes. Stop them and retry:"$'\n'"$out"
            fi
        fi
    fi
}

stop_persistenced()  { systemctl stop  nvidia-persistenced 2>/dev/null || log "nvidia-persistenced not running"; sleep 0.5; }
start_persistenced() { systemctl start nvidia-persistenced 2>/dev/null || log "nvidia-persistenced not started (may be unmanaged)"; }

# dcgm-exporter: best-effort container restart. Matches dcgm_exporter::stop/start in the Rust code.
DCGM_CONTAINER="${DCGM_EXPORTER_CONTAINER:-dcgm-exporter}"
stop_dcgm()  { docker stop  "$DCGM_CONTAINER" >/dev/null 2>&1 && log "stopped $DCGM_CONTAINER container"  || true; }
start_dcgm() { docker start "$DCGM_CONTAINER" >/dev/null 2>&1 && log "started $DCGM_CONTAINER container" || true; }

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

cmd_status() {
    printf "%-16s %-10s %-10s %-12s\n" "BDF (root)" "GPU drv" "Aud drv" "Persistence"
    for root in "$@"; do
        local gpu="${root}.0" aud="${root}.1"
        local gpu_drv aud_drv pm
        gpu_drv=$(current_driver "$gpu")
        if device_exists "$aud"; then aud_drv=$(current_driver "$aud"); else aud_drv="(no aud)"; fi
        pm="-"
        if [[ "$gpu_drv" == "nvidia" ]] && command -v nvidia-smi >/dev/null 2>&1; then
            pm=$(nvidia-smi -i "$gpu" --query-gpu=persistence_mode --format=csv,noheader 2>/dev/null || echo "?")
        fi
        printf "%-16s %-10s %-10s %-12s\n" "$root" "$gpu_drv" "$aud_drv" "$pm"
    done
}

cmd_vfio() {
    require_root
    local roots=("$@")

    # Pre-flight: every GPU must exist.
    for root in "${roots[@]}"; do
        device_exists "${root}.0" || die "PCI device ${root}.0 not found"
    done

    # Short-circuit if all already on vfio-pci (audio too, where present).
    local need_switch=false
    for root in "${roots[@]}"; do
        [[ "$(current_driver "${root}.0")" == "vfio-pci" ]] || { need_switch=true; break; }
        if device_exists "${root}.1"; then
            [[ "$(current_driver "${root}.1")" == "vfio-pci" ]] || { need_switch=true; break; }
        fi
    done
    if ! $need_switch; then
        log "all GPUs (and audio functions) already on vfio-pci"
        return 0
    fi

    ensure_module vfio-pci
    stop_dcgm

    # Per-GPU: disable MIG, SR-IOV, persistence mode (best-effort).
    for root in "${roots[@]}"; do
        local gpu="${root}.0"
        if command -v nvidia-smi >/dev/null 2>&1 && [[ "$(current_driver "$gpu")" == "nvidia" ]]; then
            local mig
            mig=$(nvidia-smi -i "$gpu" --query-gpu=mig.mode.current --format=csv,noheader 2>/dev/null || echo "")
            if [[ "$mig" == "Enabled" ]]; then
                log "disabling SR-IOV VFs on $gpu"
                if command -v nvidia-sriov-manage >/dev/null 2>&1; then
                    nvidia-sriov-manage -d -i "$gpu" || warn "nvidia-sriov-manage disable failed on $gpu"
                else
                    echo 0 > "/sys/bus/pci/devices/$gpu/sriov_numvfs" 2>/dev/null || true
                fi
                log "disabling MIG on $gpu"
                nvidia-smi -i "$gpu" -mig 0 || warn "failed to disable MIG on $gpu"
            fi
            log "disabling persistence mode on $gpu"
            nvidia-smi -i "$gpu" -pm 0 || warn "failed to disable persistence on $gpu"
        fi
    done

    stop_persistenced
    try_unload_modules
    ensure_gpu_available "${roots[@]}"

    # Unbind (audio first, then GPU) and bind to vfio-pci.
    for root in "${roots[@]}"; do
        if device_exists "${root}.1"; then unbind_if_bound "${root}.1"; fi
        unbind_if_bound "${root}.0"
    done
    for root in "${roots[@]}"; do
        if device_exists "${root}.1"; then bind_to_driver "${root}.1" vfio-pci; fi
        bind_to_driver "${root}.0" vfio-pci
    done

    log "waiting for vfio-pci to settle..."
    sleep 2
    log "done. All listed GPUs now on vfio-pci."
}

cmd_nvidia() {
    require_root
    local verify_cuda=false
    local roots=()
    for arg in "$@"; do
        case "$arg" in
            --verify-cuda) verify_cuda=true ;;
            *)             roots+=("$arg") ;;
        esac
    done
    (( ${#roots[@]} > 0 )) || die "no BDFs given"

    for root in "${roots[@]}"; do
        device_exists "${root}.0" || die "PCI device ${root}.0 not found"
    done

    # Determine if a rebind is needed.
    local need_switch=false
    for root in "${roots[@]}"; do
        [[ "$(current_driver "${root}.0")" == "nvidia" ]] || { need_switch=true; break; }
        if device_exists "${root}.1"; then
            [[ "$(current_driver "${root}.1")" == "snd_hda_intel" ]] || { need_switch=true; break; }
        fi
    done

    if $need_switch; then
        for root in "${roots[@]}"; do
            if device_exists "${root}.1"; then unbind_if_bound "${root}.1"; fi
            unbind_if_bound "${root}.0"
        done
        for root in "${roots[@]}"; do
            bind_to_driver "${root}.0" nvidia
            if device_exists "${root}.1"; then bind_to_driver "${root}.1" snd_hda_intel; fi
            if command -v nvidia-smi >/dev/null 2>&1; then
                nvidia-smi -i "${root}.0" -pm 0 >/dev/null 2>&1 || true
            fi
        done
        # Best-effort light reset.
        for root in "${roots[@]}"; do
            [[ -e "/sys/bus/pci/devices/${root}.0/reset" ]] && echo 1 > "/sys/bus/pci/devices/${root}.0/reset" 2>/dev/null || true
            if device_exists "${root}.1"; then
                [[ -e "/sys/bus/pci/devices/${root}.1/reset" ]] && echo 1 > "/sys/bus/pci/devices/${root}.1/reset" 2>/dev/null || true
            fi
        done
    else
        log "all GPUs (and audio functions) already on host drivers"
    fi

    start_persistenced

    if command -v nvidia-smi >/dev/null 2>&1; then
        for root in "${roots[@]}"; do
            log "enabling persistence mode on ${root}.0"
            nvidia-smi -i "${root}.0" -pm 1 || warn "failed to enable persistence on ${root}.0"
        done
        # Triggers UUID population in /proc/driver/nvidia/gpus/<bdf>/information.
        log "running nvidia-smi to populate GPU state..."
        nvidia-smi -L >/dev/null || warn "nvidia-smi -L failed"
    fi

    start_dcgm

    if $verify_cuda; then
        command -v nvidia-smi >/dev/null 2>&1 || die "--verify-cuda requires nvidia-smi"
        for root in "${roots[@]}"; do
            log "verifying CUDA readiness on ${root}.0..."
            local ok=false
            for _ in 1 2 3 4 5; do
                if nvidia-smi -i "${root}.0" --query-gpu=uuid --format=csv,noheader >/dev/null 2>&1; then
                    ok=true; break
                fi
                sleep 0.5
            done
            $ok || die "GPU ${root}.0 did not become CUDA-ready after retries"
        done
        log "all GPUs verified CUDA-ready"
    fi

    log "done. All listed GPUs now on the nvidia driver."
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
usage() {
    sed -n '2,16p' "$0"
    exit 1
}

(( $# >= 2 )) || usage
sub="$1"; shift

case "$sub" in
    vfio|nvidia|status) ;;
    -h|--help|help)     usage ;;
    *) die "unknown subcommand: $sub (expected vfio|nvidia|status)" ;;
esac

roots=()
extra=()
for arg in "$@"; do
    if [[ "$arg" == --* ]]; then
        extra+=("$arg")
    else
        roots+=("$(normalize_bdf "$arg")")
    fi
done
(( ${#roots[@]} > 0 )) || die "no BDFs given"

case "$sub" in
    status) cmd_status "${roots[@]}" ;;
    vfio)   cmd_vfio   "${roots[@]}" ;;
    nvidia) cmd_nvidia "${roots[@]}" "${extra[@]}" ;;
esac
