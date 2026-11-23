#!/bin/sh
# Writes site/config.js from SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY / HCAPTCHA_SITE_KEY and cache-busts index.html with the commit, so
# browsers don't keep running an old app.js / style.css after a deploy. Used by both deploy workflows.
# Usage (in CI): sh scripts/write-site-config.sh
set -e
# HCAPTCHA_SITE_KEY (optional, public): the hCaptcha site key, when Supabase has captcha protection on.
# GOOGLE_SIGNIN (optional): "Continue with Google" is shown unless this is "false" (Google is set up in Supabase, see SETUP.md).
google=true; [ "$GOOGLE_SIGNIN" = "false" ] && google=false
printf 'window.KINO_CONFIG = { supabaseUrl: "%s", supabaseKey: "%s", captchaSiteKey: "%s", googleSignIn: %s };\n' \
  "$SUPABASE_URL" "$SUPABASE_PUBLISHABLE_KEY" "$HCAPTCHA_SITE_KEY" "$google" > site/config.js
v=$(printf '%s' "$GITHUB_SHA" | cut -c1-8)
sed -i -e "s#src=\"app.js\"#src=\"app.js?v=$v\"#" -e "s#href=\"style.css\"#href=\"style.css?v=$v\"#" \
       -e "s#src=\"config.js\"#src=\"config.js?v=$v\"#" site/index.html
