#!/usr/bin/env sh
# Inicia o ShadowIADetector (agente local + dashboard)
cd "$(dirname "$0")" || exit 1
exec python3 -m shadowia_detector run --open "$@"
