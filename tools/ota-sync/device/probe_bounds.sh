#!/system/bin/sh
# probe_bounds.sh — 探测每个分段已写入前缀的末端（64 KiB 块粒度）
#
# 更新器把文件切成若干段并行写，每段内部顺序写入，故每段的有效数据是一个
# 连续前缀。本脚本逐块探测该前缀末端，输出块号（由宿主机换算成字节）。
#
# 注意：设备 mksh 的 $(( )) 与 [ ] 是 32 位有符号，5 GB 级字节数会溢出。
#       故全程使用 64 KiB 块号（< 2^31），由 dd 自行换算成字节偏移。
#
# 用法: probe_bounds.sh FILE "sblk:eblk:lastblk" [...]
#   sblk/eblk 为段 [start, end) 对应的块号范围（eblk 为开区间上界）
#   lastblk 为上次已拷到的块号
F="$1"
shift

[ -n "$F" ] || exit 2
[ -f "$F" ] || exit 3

blk_nonzero() {
  n=$(dd if="$F" bs=65536 skip="$1" count=1 2>/dev/null | tr -d '\0' | wc -c)
  [ "$n" -gt 0 ]
}

for spec in "$@"; do
  s=${spec%%:*}
  rest=${spec#*:}
  e=${rest%%:*}
  last=${rest#*:}
  [ -z "$last" ] && last=$s

  a=$s
  [ "$last" -gt "$a" ] && a=$last
  hi=$((e - 1))
  if [ "$hi" -lt "$a" ]; then
    echo "$s"
    continue
  fi
  if ! blk_nonzero "$a"; then
    echo "$a"
    continue
  fi

  # 指数扩张找到第一个空块，再二分。
  step=1
  while [ $((a + step)) -le "$hi" ] && blk_nonzero $((a + step)); do
    a=$((a + step))
    step=$((step * 2))
  done
  b=$((a + step))
  [ "$b" -gt "$e" ] && b=$e

  while [ $((b - a)) -gt 1 ]; do
    mid=$(( (a + b) / 2 ))
    [ "$mid" -le "$a" ] && mid=$((a + 1))
    if blk_nonzero "$mid"; then
      a=$mid
    else
      b=$mid
    fi
  done

  r=$((a + 1))
  [ "$r" -gt "$e" ] && r=$e
  echo "$r"
done
