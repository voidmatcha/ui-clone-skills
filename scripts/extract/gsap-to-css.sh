#!/usr/bin/env bash
# gsap-to-css.sh — Convert GSAP easing names to CSS cubic-bezier values
# Usage:
#   bash scripts/extract/gsap-to-css.sh power5         → cubic-bezier(0.05, 0.86, 0.09, 1)
#   bash scripts/extract/gsap-to-css.sh all            → print full table
#   bash scripts/extract/gsap-to-css.sh scan file.js   → find all easings in a bundle

set -eo pipefail

lookup() {
  case "$1" in
    power1|power1.out)    echo "cubic-bezier(0.25, 1, 0.5, 1)" ;;
    power1.in)            echo "cubic-bezier(0.5, 0, 0.75, 0)" ;;
    power1.inOut)         echo "cubic-bezier(0.45, 0, 0.55, 1)" ;;
    power2|power2.out)    echo "cubic-bezier(0.22, 1, 0.36, 1)" ;;
    power2.in)            echo "cubic-bezier(0.64, 0, 0.78, 0)" ;;
    power2.inOut)         echo "cubic-bezier(0.45, 0, 0.55, 1)" ;;
    power3|power3.out)    echo "cubic-bezier(0.16, 1, 0.3, 1)" ;;
    power3.in)            echo "cubic-bezier(0.7, 0, 0.84, 0)" ;;
    power3.inOut)         echo "cubic-bezier(0.65, 0, 0.35, 1)" ;;
    power4|power4.out)    echo "cubic-bezier(0.08, 0.9, 0.15, 1)" ;;
    power4.in)            echo "cubic-bezier(0.85, 0, 0.92, 0.1)" ;;
    power5|power5.out)    echo "cubic-bezier(0.05, 0.86, 0.09, 1)" ;;
    circ|circ.out)        echo "cubic-bezier(0, 0.55, 0.45, 1)" ;;
    circ.in)              echo "cubic-bezier(0.55, 0, 1, 0.45)" ;;
    circ.inOut)           echo "cubic-bezier(0.85, 0, 0.15, 1)" ;;
    circ2|circ3)          echo "cubic-bezier(0.08, 0.82, 0.17, 1)" ;;
    expo|expo.out|expo2)  echo "cubic-bezier(0.16, 1, 0.3, 1)" ;;
    expo.in)              echo "cubic-bezier(0.7, 0, 0.84, 0)" ;;
    expo.inOut)           echo "cubic-bezier(0.87, 0, 0.13, 1)" ;;
    back|back.out)        echo "cubic-bezier(0.34, 1.56, 0.64, 1)" ;;
    back.in)              echo "cubic-bezier(0.36, 0, 0.66, -0.56)" ;;
    none|linear)          echo "linear" ;;
    ease)                 echo "cubic-bezier(0.25, 0.1, 0.25, 1)" ;;
    *)                    echo "UNKNOWN" ;;
  esac
}

case "${1:-}" in
  all)
    echo "GSAP Easing → CSS cubic-bezier"
    echo "═══════════════════════════════════════════════════"
    for e in power1 power2 power3 power4 power5 circ circ2 expo back linear; do
      printf "  %-16s → %s\n" "$e" "$(lookup "$e")"
    done
    ;;

  scan)
    FILE="${2:?Usage: gsap-to-css.sh scan <bundle-file>}"
    [ -f "$FILE" ] || { echo "File not found: $FILE" >&2; exit 1; }
    echo "Scanning $(basename "$FILE") for GSAP easings..."
    echo ""
    FOUND=$(grep -oE 'ease:\s*"[^"]*"' "$FILE" 2>/dev/null | sort -u || true)
    [ -z "$FOUND" ] && { echo "No ease values found"; exit 0; }
    echo "$FOUND" | while IFS= read -r line; do
      EASE=$(echo "$line" | grep -oE '"[^"]*"' | tr -d '"')
      CSS=$(lookup "$EASE")
      printf "  %-30s → %s\n" "$EASE" "$CSS"
    done
    ;;

  "")
    echo "Usage:"
    echo "  gsap-to-css.sh <name>          Convert one easing"
    echo "  gsap-to-css.sh all             Print full table"
    echo "  gsap-to-css.sh scan <file.js>  Scan bundle for easings"
    exit 1
    ;;

  *)
    CSS=$(lookup "$1")
    if [ "$CSS" = "UNKNOWN" ]; then
      echo "Unknown easing: $1" >&2
      exit 1
    fi
    echo "$CSS"
    ;;
esac
