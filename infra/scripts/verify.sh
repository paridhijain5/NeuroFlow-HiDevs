#!/usr/bin/env bash
# Checks the Task 15 checklist against the RUNNING prod stack. Run from the repo root:
#   bash infra/scripts/verify.sh
set -u
API=neuroflow-api-1
FAILS=0
ok()   { echo "  PASS  $1"; }
bad()  { echo "  FAIL  $1"; FAILS=$((FAILS+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "1. image size < 500MB"
SIZE=$(docker image inspect neuroflow-api:latest --format '{{.Size}}')
check "neuroflow-api:latest is $((SIZE/1024/1024)) MB" "[ $SIZE -lt $((500*1024*1024)) ]"

echo "2. non-root"
check "whoami -> neuroflow" "[ \"\$(docker exec $API whoami)\" = neuroflow ]"

echo "3. read-only root fs"
check "touch /test is denied" "! docker exec $API touch /test 2>/dev/null"
check "/tmp is writable (tmpfs)" "docker exec $API touch /tmp/ok"
check "HostConfig.ReadonlyRootfs = true" "[ \"\$(docker inspect $API --format '{{.HostConfig.ReadonlyRootfs}}')\" = true ]"

echo "4. capabilities"
check "CapDrop contains ALL" "docker inspect $API --format '{{.HostConfig.CapDrop}}' | grep -q ALL"
check "no-new-privileges set" "docker inspect $API --format '{{.HostConfig.SecurityOpt}}' | grep -q no-new-privileges"

echo "5. health check"
check "docker inspect shows Health" "docker inspect $API | grep -q Health"
check "status is healthy" "[ \"\$(docker inspect $API --format '{{.State.Health.Status}}')\" = healthy ]"

echo "6. load balancing across API replicas (via X-Upstream-Addr)"
N=$(for i in $(seq 8); do curl -sk -D - -o /dev/null https://localhost/api/health | grep -i '^x-upstream-addr'; done | tr -d '\r' | sort -u | wc -l)
check "requests served by $N distinct replicas (need 2)" "[ $N -ge 2 ]"
sleep 3

echo "7. Nginx rate limiting"
CODES=$(for i in $(seq 100); do curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/api/health; done)
check "429 returned when limit exceeded" "echo \"$CODES\" | grep -q 429"

echo
[ $FAILS -eq 0 ] && echo "ALL CHECKS PASSED" || { echo "$FAILS check(s) failed"; exit 1; }
