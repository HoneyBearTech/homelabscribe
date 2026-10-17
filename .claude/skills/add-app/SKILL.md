---
name: add-app
description: Add a self-hosted app to HomelabScribe's built-in catalog so it gets a friendly name, category and plain-English description. Use when asked to recognize or describe a new app or image.
argument-hint: "[app or image name]"
---

Add `$ARGUMENTS` to `src/homelabscribe/catalog.yaml`.

1. Find the image name(s) people actually run (official image plus common linuxserver/ghcr variants).
   The `match` list holds image **base names** (no registry, owner or tag); `enrich.py` also tries the
   image owner, for images named like `vaultwarden/server`.
2. Pick an existing category if one fits (Media, Downloads, Photos, Network, Infrastructure,
   Dashboards, Monitoring, Home, Security, Files, Development, Databases).
3. Write the description for a non-technical family member: one short sentence about what it does
   for the household, and what breaks if it's down when that matters.
4. Keep entries grouped by category, one per line, matching the existing format.
5. Add a case to `test_image_names` or `test_enrich_catalog_labels_and_traefik` if the image name is
   unusual, then run `/check`.
