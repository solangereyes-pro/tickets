#!/bin/bash
# Script de inicio del robot ServiceTonic Monitor
# Uso: ./start_robot.sh [--background]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD="${PYTHON_CMD:-python3}"
PID_FILE="$SCRIPT_DIR/robot.pid"
LOG_FILE="$SCRIPT_DIR/output.log"

start_background() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "El robot ya está en ejecución (PID: $PID)"
            exit 0
        fi
    fi
    
    echo "Iniciando robot en segundo plano..."
    cd "$SCRIPT_DIR"
    nohup $PYTHON_CMD servicetonic_monitor.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Robot iniciado con PID: $(cat $PID_FILE)"
    echo "Logs en: $LOG_FILE"
}

stop_robot() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Deteniendo robot (PID: $PID)..."
            kill "$PID"
            rm -f "$PID_FILE"
            echo "Robot detenido."
        else
            echo "El robot no está en ejecución."
            rm -f "$PID_FILE"
        fi
    else
        echo "No se encontró archivo PID. El robot puede no estar en ejecución."
    fi
}

status_robot() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Robot en ejecución (PID: $PID)"
        else
            echo "Robot detenido (PID obsoleto encontrado)"
            rm -f "$PID_FILE"
        fi
    else
        echo "Robot no está en ejecución."
    fi
}

case "${1:-}" in
    --background|-b|start)
        start_background
        ;;
    stop)
        stop_robot
        ;;
    status)
        status_robot
        ;;
    restart)
        stop_robot
        sleep 2
        start_background
        ;;
    *)
        # Ejecución en primer plano (por defecto)
        echo "Iniciando robot en primer plano (Ctrl+C para detener)..."
        cd "$SCRIPT_DIR"
        exec $PYTHON_CMD servicetonic_monitor.py
        ;;
esac
