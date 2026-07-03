#!/bin/bash
# Build moving-target Gecko codes for Dolphin.
#
# Primary output: moving_targets_keyframe.gecko.txt
#   - Assembles gecko/mover_install.s
#   - Runs gen_moving_gct.py to randomize all 25 Target Test stages and pack data
#
# Optional: ./build.sh --engine also builds moving_targets_engine.gecko.txt
#
# Requires devkitPPC: https://devkitpro.org/
set -e
cd "$(dirname "$0")"

BUILD_ENGINE=0
GEN_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --engine) BUILD_ENGINE=1 ;;
        *) GEN_ARGS+=("$arg") ;;
    esac
done

./gecko/build_install.sh
python3 gen_moving_gct.py "${GEN_ARGS[@]}"

if [ "$BUILD_ENGINE" -eq 1 ]; then
    ./gecko/build_engine.sh
fi
