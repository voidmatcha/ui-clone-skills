# Helpers for loading project-local dynamic selector masks for section-compare.sh.
# The format is intentionally data-only: one selector per line or comma-separated
# selectors; blank lines and full-line comments are ignored. Do not source
# project files as shell.

_section_dynamic_trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

_section_dynamic_append_selector() {
  local selector
  selector="$(_section_dynamic_trim "$1")"
  [ -n "$selector" ] || return 0
  if [ -n "${_SECTION_DYNAMIC_EXTRA:-}" ]; then
    _SECTION_DYNAMIC_EXTRA="${_SECTION_DYNAMIC_EXTRA}, ${selector}"
  else
    _SECTION_DYNAMIC_EXTRA="$selector"
  fi
}

_section_dynamic_load_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  local line part
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(_section_dynamic_trim "$line")"
    [ -n "$line" ] || continue
    case "$line" in
      \#*) continue ;;
    esac
    local old_ifs="$IFS"
    IFS=','
    # shellcheck disable=SC2206
    local parts=( $line )
    IFS="$old_ifs"
    for part in "${parts[@]}"; do
      _section_dynamic_append_selector "$part"
    done
  done < "$file"
}

_section_fixed_append_selector() {
  local selector
  selector="$(_section_dynamic_trim "$1")"
  [ -n "$selector" ] || return 0
  if [ -n "${_SECTION_FIXED_EXTRA:-}" ]; then
    _SECTION_FIXED_EXTRA="${_SECTION_FIXED_EXTRA}, ${selector}"
  else
    _SECTION_FIXED_EXTRA="$selector"
  fi
}

_section_fixed_load_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  local line part
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(_section_dynamic_trim "$line")"
    [ -n "$line" ] || continue
    case "$line" in
      \#*) continue ;;
    esac
    local old_ifs="$IFS"
    IFS=','
    # shellcheck disable=SC2206
    local parts=( $line )
    IFS="$old_ifs"
    for part in "${parts[@]}"; do
      _section_fixed_append_selector "$part"
    done
  done < "$file"
}

_section_ignore_append_selector() {
  local selector
  selector="$(_section_dynamic_trim "$1")"
  [ -n "$selector" ] || return 0
  if [ -n "${_SECTION_IGNORE_EXTRA:-}" ]; then
    _SECTION_IGNORE_EXTRA="${_SECTION_IGNORE_EXTRA}, ${selector}"
  else
    _SECTION_IGNORE_EXTRA="$selector"
  fi
}

_section_ignore_load_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  local line part
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(_section_dynamic_trim "$line")"
    [ -n "$line" ] || continue
    case "$line" in
      \#*) continue ;;
    esac
    local old_ifs="$IFS"
    IFS=','
    # shellcheck disable=SC2206
    local parts=( $line )
    IFS="$old_ifs"
    for part in "${parts[@]}"; do
      _section_ignore_append_selector "$part"
    done
  done < "$file"
}

load_section_dynamic_selectors_config() {
  _SECTION_DYNAMIC_EXTRA=""
  _SECTION_FIXED_EXTRA=""
  _SECTION_IGNORE_EXTRA=""
  local files=()
  if [ -n "${SECTION_DYNAMIC_SELECTORS_FILE:-}" ]; then
    files+=("$SECTION_DYNAMIC_SELECTORS_FILE")
  else
    files+=("$PWD/.visual-debug/section-dynamic-selectors.txt")
    files+=("$PWD/.visual-debug/section-compare-dynamic-selectors.txt")
    if [ -n "${REF_ROOT_DIR:-}" ]; then
      files+=("$REF_ROOT_DIR/.visual-debug/section-dynamic-selectors.txt")
      files+=("$REF_ROOT_DIR/section-dynamic-selectors.txt")
    fi
    if [ -n "${DIR:-}" ]; then
      files+=("$DIR/.visual-debug/section-dynamic-selectors.txt")
      files+=("$DIR/section-dynamic-selectors.txt")
    fi
  fi

  local file seen=""
  for file in "${files[@]}"; do
    [ -n "$file" ] || continue
    case ",$seen," in
      *,"$file",*) continue ;;
    esac
    seen="${seen},${file}"
    _section_dynamic_load_file "$file"
  done

  if [ -n "$_SECTION_DYNAMIC_EXTRA" ]; then
    if [ -n "${DYNAMIC_SELECTORS:-}" ]; then
      DYNAMIC_SELECTORS="${DYNAMIC_SELECTORS}, ${_SECTION_DYNAMIC_EXTRA}"
    else
      DYNAMIC_SELECTORS="$_SECTION_DYNAMIC_EXTRA"
    fi
    export DYNAMIC_SELECTORS
  fi
  local fixed_files=()
  if [ -n "${SECTION_FIXED_OVERLAY_SELECTORS_FILE:-}" ]; then
    fixed_files+=("$SECTION_FIXED_OVERLAY_SELECTORS_FILE")
  else
    fixed_files+=("$PWD/.visual-debug/section-fixed-overlay-selectors.txt")
    fixed_files+=("$PWD/.visual-debug/section-compare-fixed-overlay-selectors.txt")
    if [ -n "${REF_ROOT_DIR:-}" ]; then
      fixed_files+=("$REF_ROOT_DIR/.visual-debug/section-fixed-overlay-selectors.txt")
      fixed_files+=("$REF_ROOT_DIR/section-fixed-overlay-selectors.txt")
    fi
    if [ -n "${DIR:-}" ]; then
      fixed_files+=("$DIR/.visual-debug/section-fixed-overlay-selectors.txt")
      fixed_files+=("$DIR/section-fixed-overlay-selectors.txt")
    fi
  fi

  seen=""
  for file in "${fixed_files[@]}"; do
    [ -n "$file" ] || continue
    case ",$seen," in
      *,"$file",*) continue ;;
    esac
    seen="${seen},${file}"
    _section_fixed_load_file "$file"
  done

  if [ -n "$_SECTION_FIXED_EXTRA" ]; then
    if [ -n "${SECTION_FIXED_OVERLAY_SELECTORS:-}" ]; then
      SECTION_FIXED_OVERLAY_SELECTORS="${SECTION_FIXED_OVERLAY_SELECTORS}, ${_SECTION_FIXED_EXTRA}"
    else
      SECTION_FIXED_OVERLAY_SELECTORS="$_SECTION_FIXED_EXTRA"
    fi
    export SECTION_FIXED_OVERLAY_SELECTORS
  fi
  local ignore_files=()
  if [ -n "${SECTION_IGNORE_SELECTORS_FILE:-}" ]; then
    ignore_files+=("$SECTION_IGNORE_SELECTORS_FILE")
  else
    ignore_files+=("$PWD/.visual-debug/section-ignore-selectors.txt")
    ignore_files+=("$PWD/.visual-debug/section-compare-ignore-selectors.txt")
    if [ -n "${REF_ROOT_DIR:-}" ]; then
      ignore_files+=("$REF_ROOT_DIR/.visual-debug/section-ignore-selectors.txt")
      ignore_files+=("$REF_ROOT_DIR/section-ignore-selectors.txt")
    fi
    if [ -n "${DIR:-}" ]; then
      ignore_files+=("$DIR/.visual-debug/section-ignore-selectors.txt")
      ignore_files+=("$DIR/section-ignore-selectors.txt")
    fi
  fi

  seen=""
  for file in "${ignore_files[@]}"; do
    [ -n "$file" ] || continue
    case ",$seen," in
      *,"$file",*) continue ;;
    esac
    seen="${seen},${file}"
    _section_ignore_load_file "$file"
  done

  if [ -n "$_SECTION_IGNORE_EXTRA" ]; then
    if [ -n "${SECTION_IGNORE_SELECTORS:-}" ]; then
      SECTION_IGNORE_SELECTORS="${SECTION_IGNORE_SELECTORS}, ${_SECTION_IGNORE_EXTRA}"
    else
      SECTION_IGNORE_SELECTORS="$_SECTION_IGNORE_EXTRA"
    fi
    export SECTION_IGNORE_SELECTORS
  fi
  unset _SECTION_DYNAMIC_EXTRA
  unset _SECTION_FIXED_EXTRA
  unset _SECTION_IGNORE_EXTRA
}
