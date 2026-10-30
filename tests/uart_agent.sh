#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Private, per-run UART dispatcher. Only checksum-verified files are executed.
root=${0%/*}
session=$1
prefix="@DSA_$session"
released=0
saved=$(stty -g) || exit 1
restore() { stty "$saved"; }
trap restore EXIT
trap 'exit 1' HUP INT TERM
stty -echo -onlcr || exit 1
emit() {
    run="$root/r$1"
    if [ ! -f "$run/done" ]; then
        printf '%s MISSING %s\n' "$prefix" "$1"
        return
    fi
    read -r rc size digest < "$run/done"
    printf '%s BEGIN %s %s %s %s\n' "$prefix" "$1" "$rc" "$size" "$digest"
    base64 "$run/output"
    printf '%s END %s\n' "$prefix" "$1"
}
printf '%s READY\n' "$prefix"
while read -r action id part digest data; do
    case "$id" in ''|*[!0-9]*) continue ;; esac
    case "$action" in
    P)
        case "$part" in ''|*[!0-9]*) continue ;; esac
        dir="$root/s$id"
        mkdir -p "$dir" || continue
        if printf '%s' "$data" | base64 -d > "$dir/$part.tmp"; then
            actual=$(sha256sum "$dir/$part.tmp")
            actual=${actual%% *}
            if [ "$actual" = "$digest" ]; then
                mv "$dir/$part.tmp" "$dir/$part" || continue
                printf '%s ACK %s %s %s\n' "$prefix" "$id" "$part" "$digest"
            fi
        fi
        ;;
    X)
        # A repeated ID can only retrieve its cached result, never execute twice.
        if [ "$id" -le "$released" ]; then
            printf '%s UNKNOWN %s\n' "$prefix" "$id"
            continue
        fi
        run="$root/r$id"
        if [ -f "$run/done" ]; then emit "$id"; continue; fi
        if [ -e "$run/started" ]; then
            printf '%s UNKNOWN %s\n' "$prefix" "$id"
            continue
        fi
        case "$part:$data" in *[!0-9:]*|:*) continue ;; esac
        [ -n "$part" ] && [ -n "$data" ] || continue
        dir="$root/s$part"
        if [ ! -f "$dir/script" ]; then
            : > "$dir/script.tmp" || continue
            index=0
            while [ "$index" -lt "$data" ]; do
                cat "$dir/$index" >> "$dir/script.tmp" || break
                index=$((index + 1))
            done
            actual=$(sha256sum "$dir/script.tmp")
            actual=${actual%% *}
            if [ "$index" -ne "$data" ] || [ "$actual" != "$digest" ]; then
                printf '%s INVALID %s\n' "$prefix" "$id"
                continue
            fi
            mv "$dir/script.tmp" "$dir/script" || continue
        fi
        actual=$(sha256sum "$dir/script")
        actual=${actual%% *}
        if [ "$actual" != "$digest" ]; then
            printf '%s INVALID %s\n' "$prefix" "$id"
            continue
        fi
        mkdir -p "$run" || continue
        : > "$run/started" || continue
        # A script cannot consume protocol input or terminate this dispatcher.
        sh "$dir/script" </dev/null > "$run/output" 2>&1
        rc=$?
        size=$(wc -c < "$run/output")
        # Protect the exit status too: a corrupted 1 -> 0 must not turn a
        # failed command into a successful test setup.
        actual=$({ printf '%s\n' "$rc"; cat "$run/output"; } | sha256sum)
        actual=${actual%% *}
        printf '%s %s %s\n' "$rc" "$size" "$actual" > "$run/done.tmp"
        mv "$run/done.tmp" "$run/done" || continue
        emit "$id"
        ;;
    R) emit "$id" ;;
    D)
        # Verified results are durable on the host; keep a high-water mark
        # instead of unbounded output files or per-command tombstone inodes.
        while [ "$released" -lt "$id" ]; do
            released=$((released + 1))
            rm -rf "$root/r$released"
        done
        ;;
    F) rm -rf "$root/s$id" ;;
    Q)
        # root is the path used to start this verified, uniquely named agent.
        rm -rf "$root"
        restore
        trap - EXIT
        printf '%s BYE\n' "$prefix"
        exit 0
        ;;
    esac
done
