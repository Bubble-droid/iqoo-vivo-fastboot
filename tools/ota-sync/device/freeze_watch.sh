#!/system/bin/sh
# freeze_watch.sh — 下载完成/开始校验的瞬间冻结更新器，阻止其自动重启
#
# 触发条件（任一）：
#   1. 更新器读取量突增（rchar 增量 > 32 MiB / 0.2 s），即开始校验整包；
#   2. 文件尺寸达到总大小、已分配块数接近总量且稳定，即各段皆已写完。
# 冻结后写标记文件，宿主机据此收尾。
#
# 注意：设备 mksh 的 $(( )) 与 [ ] 是 32 位有符号，5 GB 级数字会溢出；
#       此处大数一律交给 awk。
#
# 用法: freeze_watch.sh FILE TOTAL MARK LOG PACKAGE
F="$1"
TOTAL="$2"
MARK="$3"
LOG="$4"
PKG="$5"

[ -n "$F" ] && [ -n "$TOTAL" ] && [ -n "$MARK" ] || exit 2
rm -f "$MARK"
: > "$LOG"

gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }'; }
ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a >= b) }'; }

# 用 ps 的完整命令行匹配包名及其子进程（如 :remote）。
pids_of() {
  if [ -n "$PKG" ]; then
    ps -A -o PID,ARGS 2>/dev/null | awk -v p="$PKG" '
      NR > 1 {
        name = $2
        if (name == p || index(name, p ":") == 1) print $1
      }'
  fi
}

THRESH=$(awk -v t="$TOTAL" 'BEGIN { printf "%.0f", t - 33554432 }')
prev=0
last_alloc=0
stable=0
first=1
tick=0

while :; do
  tick=$((tick + 1))
  pids=$(pids_of)
  if [ -z "$pids" ]; then
    sleep 0.2
    continue
  fi

  rb=0
  wb=0
  for p in $pids; do
    v=$(sed -n 's/^rchar: //p' /proc/$p/io 2>/dev/null)
    [ -n "$v" ] && rb=$(awk -v a="$rb" -v b="$v" 'BEGIN { printf "%.0f", a + b }')
    v=$(sed -n 's/^wchar: //p' /proc/$p/io 2>/dev/null)
    [ -n "$v" ] && wb=$(awk -v a="$wb" -v b="$v" 'BEGIN { printf "%.0f", a + b }')
  done

  b=$(stat -c %b "$F" 2>/dev/null) || b=0
  alloc=$(awk -v b="$b" 'BEGIN { printf "%.0f", b * 512 }')
  s=$(stat -c %s "$F" 2>/dev/null) || s=0

  if [ "$first" = 1 ]; then
    prev=$rb
    last_alloc=$alloc
    first=0
    sleep 0.2
    continue
  fi

  if gt "$alloc" "$last_alloc"; then
    stable=0
  else
    stable=$((stable + 1))
  fi

  delta=$(awk -v a="$rb" -v b="$prev" 'BEGIN { printf "%.0f", a - b }')
  reason=""
  if ge "$s" "$TOTAL" && gt "$delta" 33554432; then
    reason=read_burst
  fi
  if ge "$s" "$TOTAL" && ge "$alloc" "$THRESH" && [ "$stable" -ge 3 ]; then
    reason=alloc_done
  fi

  if [ -n "$reason" ] || [ $((tick % 25)) -eq 0 ]; then
    echo "$(date +%H:%M:%S) rb=$rb wb=$wb alloc=$alloc size=$s stable=$stable reason=$reason" >> "$LOG"
  fi

  if [ -n "$reason" ]; then
    for p in $pids; do
      kill -STOP $p 2>/dev/null
    done
    echo "$reason $(date +%s) size=$s alloc=$alloc" > "$MARK"
    exit 0
  fi

  prev=$rb
  last_alloc=$alloc
  sleep 0.2
done
