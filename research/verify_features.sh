#!/usr/bin/env bash
# End-to-end feature smoke test for Charweb, via curl only (no browser).
# Registers a throwaway test account and walks through every major route,
# printing PASS/FAIL per feature so you can see exactly what's broken.
#
# Usage:
#   BASE_URL=https://charweb.net ./verify_features.sh
#   BASE_URL=http://127.0.0.1:8000 ./verify_features.sh    # test locally on the VM instead

set -u
BASE_URL="${BASE_URL:-https://charweb.net}"
JAR=$(mktemp)
USERNAME="verify_$(date +%s)"
EMAIL="${USERNAME}@example.com"
PASSWORD="VerifyTest123!"
PASS=0
FAIL=0

cleanup() { rm -f "$JAR"; }
trap cleanup EXIT

check() {
    local label="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  [PASS] $label (got $actual)"
        PASS=$((PASS+1))
    else
        echo "  [FAIL] $label (expected $expected, got $actual)"
        FAIL=$((FAIL+1))
    fi
}

get_csrf() {
    # Extract the hidden csrf_token field's value from a page's HTML.
    # Attribute order isn't guaranteed (id="csrf_token" name="csrf_token"
    # type="hidden" value="..."), so match anywhere within the same tag
    # rather than assuming name="..." is immediately followed by value="...".
    curl -s -b "$JAR" -c "$JAR" "$1" | grep -oP 'csrf_token"[^>]*value="\K[^"]+' | head -1
}

echo "=== Charweb feature verification against $BASE_URL ==="
echo "Test account: $USERNAME"
echo

echo "[1] Site reachable"
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/login")
check "GET /login" 200 "$code"

echo "[2] Register"
token=$(get_csrf "$BASE_URL/register")
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" \
    --data-urlencode "username=$USERNAME" \
    --data-urlencode "email=$EMAIL" \
    --data-urlencode "password=$PASSWORD" \
    --data-urlencode "password2=$PASSWORD" \
    --data-urlencode "accept_terms=y" \
    --data-urlencode "csrf_token=$token" \
    -L "$BASE_URL/register")
check "POST /register (final page after redirect to /login)" 200 "$code"

echo "[3] Login"
token=$(get_csrf "$BASE_URL/login")
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L \
    --data-urlencode "username=$USERNAME" \
    --data-urlencode "password=$PASSWORD" \
    --data-urlencode "csrf_token=$token" \
    "$BASE_URL/login")
check "POST /login (final page after redirect)" 200 "$code"

echo "[4] Home feed loads"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/home")
check "GET /home" 200 "$code"

echo "[5] Create a post"
token=$(get_csrf "$BASE_URL/home")
resp=$(curl -s -w "\n%{http_code}" -b "$JAR" -c "$JAR" -L \
    --data-urlencode "post=Automated verification post $(date)" \
    --data-urlencode "csrf_token=$token" \
    "$BASE_URL/home")
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | head -n -1)
check "POST /home (create post)" 200 "$code"

post_id=$(echo "$body" | grep -oP 'id="post-\K[0-9]+' | head -1)
if [ -n "$post_id" ]; then
    echo "  found post id: $post_id"
else
    echo "  [WARN] could not extract a post id from the response -- comment/like tests below will be skipped"
fi

echo "[6] Comment on the post"
if [ -n "${post_id:-}" ]; then
    code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L \
        --data-urlencode "body=Automated verification comment" \
        "$BASE_URL/comment/$post_id")
    check "POST /comment/$post_id" 200 "$code"
else
    echo "  [SKIP] no post id available"
fi

echo "[7] Like the post"
if [ -n "${post_id:-}" ]; then
    code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" \
        -H "Content-Type: application/json" \
        -X POST "$BASE_URL/like/$post_id")
    check "POST /like/$post_id" 200 "$code"
else
    echo "  [SKIP] no post id available"
fi

echo "[8] Explore/search"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/explore")
check "GET /explore" 200 "$code"

echo "[9] Edit profile"
token=$(get_csrf "$BASE_URL/edit_profile")
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L \
    --data-urlencode "username=$USERNAME" \
    --data-urlencode "about_me=Automated verification bio" \
    --data-urlencode "csrf_token=$token" \
    "$BASE_URL/edit_profile")
check "POST /edit_profile" 200 "$code"

echo "[10] Terms page"
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/terms")
check "GET /terms" 200 "$code"

echo "[11] Daily hub"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/daily")
check "GET /daily" 200 "$code"

echo "[12] Daily sign-in"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L -X POST "$BASE_URL/daily/signin")
check "POST /daily/signin" 200 "$code"

echo "[13] Create character"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L \
    --data-urlencode "name=VerifyHero" \
    -X POST "$BASE_URL/daily/create_character")
check "POST /daily/create_character" 200 "$code"

echo "[14] Dungeon action (explore)"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L \
    --data-urlencode "action=explore" \
    -X POST "$BASE_URL/daily/dungeon")
check "POST /daily/dungeon" 200 "$code"

echo "[15] Shop page"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/daily/shop")
check "GET /daily/shop" 200 "$code"

echo "[16] Ranking page"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/ranking")
check "GET /ranking" 200 "$code"

echo "[17] Chat page loads"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/chat/")
check "GET /chat/" 200 "$code"

echo "[18] Notifications API"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" "$BASE_URL/api/notifications")
check "GET /api/notifications" 200 "$code"

echo "[19] Logout"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$JAR" -c "$JAR" -L "$BASE_URL/logout")
check "GET /logout" 200 "$code"

echo
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
