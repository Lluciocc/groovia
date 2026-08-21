#!/usr/bin/env bash
set -euo pipefail

app="${1:-dist/Groovia.app}"
profile="${APPLE_NOTARY_PROFILE:-}"
if [[ ! -d "$app/Contents" ]]; then
  echo "Invalid application bundle: $app" >&2
  exit 2
fi
archive="${app%.app}-notarization.zip"
rm -f -- "$archive"
ditto -c -k --keepParent "$app" "$archive"

if [[ -n "$profile" ]]; then
  xcrun notarytool submit "$archive" --keychain-profile "$profile" --wait
else
  : "${APPLE_ID:?Set APPLE_ID or APPLE_NOTARY_PROFILE}"
  : "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID or APPLE_NOTARY_PROFILE}"
  : "${APPLE_APP_PASSWORD:?Set APPLE_APP_PASSWORD or APPLE_NOTARY_PROFILE}"
  xcrun notarytool submit "$archive" \
    --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait
fi
xcrun stapler staple "$app"
xcrun stapler validate "$app"
spctl --assess --type execute --verbose=2 "$app"
echo "Notarized and stapled: $app"
