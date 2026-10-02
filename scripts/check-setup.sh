#!/bin/sh
# Shows which of Cinecrab's accounts/keys are set up on GitHub. Prints names only, never values.
# Usage: sh scripts/check-setup.sh
REPO=belacmu/kinoprogram
export GH_HOST=github.com
secrets=$(gh secret list -R "$REPO" 2>/dev/null | cut -f1)
vars=$(gh variable list -R "$REPO" 2>/dev/null | cut -f1)
check() { # kind name purpose
  if printf '%s\n' "$2" | grep -qx "$3"; then echo "  ✓ $3"; else echo "  ✗ $3   ($4)"; fi
}
echo "Secrets (GitHub → Settings → Secrets and variables → Actions):"
for n in "GMAIL_USER|sending address for the daily email" "GMAIL_APP_PASSWORD|that account's app password" \
         "SUPABASE_SECRET_KEY|lets the daily job read subscribers" "TMDB_READ_TOKEN|film database lookups"; do
  check secret "$secrets" "${n%%|*}" "${n#*|}"
done
echo "Variables (public by design):"
for n in "SUPABASE_URL|Supabase project address" "SUPABASE_PUBLISHABLE_KEY|Supabase public key (sign-in on the site)"; do
  check var "$vars" "${n%%|*}" "${n#*|}"
done
if printf '%s\n' "$secrets" | grep -qx SURFSHARK_WG_CONF; then
  echo "Leftover: SURFSHARK_WG_CONF is unused; delete it: gh secret delete SURFSHARK_WG_CONF -R $REPO"
fi
