#!/bin/bash
# ==============================================================================
# SKY-CHEK BOTS MANAGEMENT TOOL (manage.sh)
# ==============================================================================
# Usage: ./manage.sh [command] [bot]
#
# Commands:
#   start, stop, restart, status, logs
#
# Bots:
#   master    - Master Billing Bot (main.py)
#   compress  - Video Compressor Bot (dowm.py)
#   all       - Both bots (only for start/stop/restart/status)

COMMAND=$1
BOT=$2

# Colors for terminal styling
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

show_help() {
    echo -e "${CYAN}Sky-Chek Bots Management Script${NC}"
    echo "Usage: ./manage.sh [command] [bot]"
    echo ""
    echo "Commands:"
    echo "  start       Start target bot service"
    echo "  stop        Stop target bot service"
    echo "  restart     Restart target bot service"
    echo "  status      Check status of target bot service"
    echo "  logs        Show recent logs for target bot service"
    echo ""
    echo "Bots:"
    echo "  master      Master Billing Bot (sky-chek.service)"
    echo "  compress    Video Compressor Bot (sky-compress.service)"
    echo "  all         Apply action to both bots (only for start, stop, restart, status)"
    echo ""
    echo "Examples:"
    echo "  ./manage.sh status master"
    echo "  ./manage.sh restart all"
    echo "  ./manage.sh logs compress"
}

get_service_name() {
    local target=$1
    if [ "$target" == "master" ]; then
        echo "sky-chek"
    elif [ "$target" == "compress" ]; then
        echo "sky-compress"
    else
        echo ""
    fi
}

if [ -z "$COMMAND" ] || [ -z "$BOT" ]; then
    show_help
    exit 1
fi

case "$COMMAND" in
    start|stop|restart|status)
        if [ "$BOT" == "all" ]; then
            for b in "master" "compress"; do
                SVC=$(get_service_name $b)
                echo -e "${YELLOW}Executing $COMMAND for $b ($SVC.service)...${NC}"
                systemctl $COMMAND $SVC
                echo -e "${GREEN}Done.${NC}"
                echo ""
            done
        else
            SVC=$(get_service_name $BOT)
            if [ -z "$SVC" ]; then
                echo -e "${RED}Error: Unknown bot target '$BOT'. Use 'master', 'compress' or 'all'.${NC}"
                exit 1
            fi
            echo -e "${YELLOW}Executing $COMMAND for $BOT ($SVC.service)...${NC}"
            systemctl $COMMAND $SVC
        fi
        ;;
    logs)
        SVC=$(get_service_name $BOT)
        if [ -z "$SVC" ]; then
            echo -e "${RED}Error: Unknown bot target '$BOT'. Use 'master' or 'compress'.${NC}"
            exit 1
        fi
        echo -e "${CYAN}Displaying last 30 logs for $BOT ($SVC.service):${NC}"
        journalctl -u $SVC -n 30 --no-pager
        ;;
    *)
        show_help
        exit 1
        ;;
esac
