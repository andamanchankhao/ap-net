#!/usr/bin/env bash
#
# AP-NET mission control.
#
# bash, not zsh: Raspberry Pi OS ships bash and not zsh, and this script has to run on
# both the development Mac and the Pi.

set -u
cd "$(dirname "$0")"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'
YELLOW='\033[1;33m'; CYAN='\033[0;36m'; DIM='\033[2m'; NC='\033[0m'

PY="${PYTHON:-python3}"

# edge_node.py exit contract (FIX_PLAN.md B4): 0 human, 1 no threat, 2 error.
# The old script treated any exit 1 as a successful negative, so a crash reported as PASS.
EXIT_HUMAN=0; EXIT_NO_THREAT=1; EXIT_ERROR=2

banner() {
    echo -e "${CYAN}================================================================================"
    echo -e "          AP-NET ANTI-POACHING CAMERA TRAP — SYSTEM CONTROLLER                   "
    echo -e "================================================================================${NC}"
    echo -e "Deployment target: ${GREEN}Huai Kha Khaeng Wildlife Sanctuary, Thailand${NC}"
    echo -e "Base station     : ${GREEN}15.606848° N, 99.318075° E${NC}"
    echo -e "Host             : ${GREEN}$(uname -s) $(uname -m)${NC}   Python: ${GREEN}$($PY -V 2>&1)${NC}"
    echo -e "${CYAN}================================================================================${NC}"
}

menu() {
    echo -e "\nSelect a scenario:"
    echo -e "  ${GREEN}[1]${NC} In-memory queue emulation      ${DIM}fastest, no sockets${NC}"
    echo -e "  ${GREEN}[2]${NC} UDP loopback emulation         ${DIM}real sockets, separate processes${NC}"
    echo -e "  ${GREEN}[3]${NC} Base station dashboard         ${DIM}http://localhost:8080${NC}"
    echo -e "  ${GREEN}[4]${NC} Edge AI pipeline on one image  ${DIM}capture -> detect -> compress${NC}"
    echo -e "\n  ${YELLOW}Raspberry Pi field hardware${NC}"
    echo -e "  ${GREEN}[5]${NC} Field node                     ${DIM}PIR -> camera -> YOLO -> transmit${NC}"
    echo -e "  ${GREEN}[6]${NC} Live preview / aim the camera  ${DIM}needs a display${NC}"
    echo -e "  ${GREEN}[7]${NC} System check                   ${DIM}camera, GPIO, model backends${NC}"
    echo -e "\n  ${GREEN}[8]${NC} Exit"
    echo -n -e "\nSelection ${YELLOW}[1-8]${NC}: "
}

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
info() { echo -e "${BLUE}[SYSTEM]${NC} $1"; }

run_edge_on_image() {
    local img="$1"
    $PY edge_node/edge_node.py --image "$img" --allow-mock
    local code=$?

    case $code in
        $EXIT_HUMAN)     pass "Human detected — payload staged for transmission." ;;
        $EXIT_NO_THREAT) pass "No threat — frame cached locally, radio stayed silent." ;;
        $EXIT_ERROR)     fail "Edge node errored (exit 2). This is NOT a clean negative." ;;
        *)               fail "Unexpected exit code: $code" ;;
    esac
    return $code
}

banner
menu
read -r choice

case "$choice" in
    1)
        info "In-memory queue emulation..."
        $PY run_demo_queue.py
        ;;
    2)
        info "UDP loopback emulation..."
        $PY run_demo.py
        ;;
    3)
        echo -n -e "Bind address ${DIM}[127.0.0.1 = local only, 0.0.0.0 = reachable on the LAN (needs a password)]${NC}: "
        read -r host; host="${host:-127.0.0.1}"
        info "Dashboard on ${host}:8080..."
        $PY base_station/dashboard_server.py --host "$host"
        ;;
    4)
        echo -n -e "Image path ${DIM}[mock_images/human_intruder_01.jpg]${NC}: "
        read -r img; img="${img:-mock_images/human_intruder_01.jpg}"
        info "Running the vision pipeline on $img..."
        run_edge_on_image "$img"
        ;;
    5)
        echo -n -e "Base station host ${DIM}[127.0.0.1]${NC}: "
        read -r bhost; bhost="${bhost:-127.0.0.1}"
        echo -n -e "Node ID ${DIM}[1]${NC}: "
        read -r nid; nid="${nid:-1}"
        echo -n -e "Trigger ${DIM}[pir / interval / manual]${NC}: "
        read -r trig; trig="${trig:-pir}"
        info "Starting field node (Camera-Trap-0${nid}) -> ${bhost}..."
        echo -e "${DIM}Ctrl+C to stop.${NC}"
        $PY edge_node/field_node.py --base-host "$bhost" --node-id "$nid" \
            --trigger "$trig" --allow-mock
        ;;
    6)
        echo -n -e "Transmit alerts to the base station? ${DIM}[y/N]${NC}: "
        read -r tx
        if [[ "$tx" =~ ^[Yy] ]]; then
            $PY edge_node/capture_webcam.py --transmit --allow-mock
        else
            $PY edge_node/capture_webcam.py --allow-mock
        fi
        ;;
    7)
        echo -e "\n${CYAN}--- Detection backends ---${NC}"
        $PY edge_node/detector.py --check
        echo -e "\n${CYAN}--- Camera backends ---${NC}"
        $PY edge_node/camera.py --check
        echo -e "\n${CYAN}--- GPIO backends ---${NC}"
        $PY edge_node/pir_sensor.py --check
        echo -e "\n${CYAN}--- Protocol self-test ---${NC}"
        $PY -c "
import base64, random, lora_protocol as lp
data = base64.b64encode(bytes(random.randrange(256) for _ in range(1044))).decode()
pkts = lp.fragment_payload(data)
s = lp.ReassemblySession()
order = pkts[:]; random.shuffle(order)
for _, p in order: s.accept(p)
ok = base64.b64encode(s.assemble()).decode() == data
print(f'  {len(pkts)} fragments, {len(pkts[0][1])} B/frame, shuffled reassembly: '
      + ('OK' if ok else 'FAILED'))
"
        ;;
    *)
        echo -e "\nExiting."
        exit 0
        ;;
esac
